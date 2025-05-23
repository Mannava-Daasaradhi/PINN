"""Case configuration: calculation domain, parameters, reference solutions, governing equations, IC/BC/OCs."""
import numpy as np
import torch
import deepxde as dde
from utils.icbcs import ScaledDirichletBC, ScaledNeumannBC, ScaledPointSetBC, ScaledPointSetOperatorBC
from scipy.interpolate import interp1d

dde.backend.set_default_backend("pytorch")

# dtype = dde.config.real(torch)
dtype = torch.float64

# R = ct.gas_constant


class Case:
    def __init__(self, args):
        self.args = args

        # ----------------------------------------------------------------------
        # define calculation domain
        self.x_l, self.x_r = 0.0, 0.0015
        self.geom = dde.geometry.Interval(self.x_l, self.x_r)

        # ----------------------------------------------------------------------
        # define the names of independents, dependents, and equations
        self.names = {
            "independents": ["x"],
            "dependents": ["T"],
            "equations": ["energy"],
            "ICBCOCs": []}

        self.icbcocs = []  # initial, boundary, observation conditions

        self.sL_infe_s = dde.Variable(args.infer_paras["sL"] * args.scales["sL"], dtype=dtype) if "sL" in args.infer_paras else None
        self.lam_infe_s = dde.Variable(args.infer_paras["lam"] * args.scales["lam"], dtype=dtype) if "lam" in args.infer_paras else None
        self.Ea_infe_s = dde.Variable(args.infer_paras["Ea"] * args.scales["Ea"], dtype=dtype) if "Ea" in args.infer_paras else None

        # ----------------------------------------------------------------------
        # define parameters
        self.W = 28.97e-3  # gas molecular weight, kg/mol
        self.lam = 2.6e-2  # thermal conductivity, W/(m-K)
        self.cp = 1000.0  # heat capacity, J/(kg-K)
        self.qF = 5.0e7  # fuel calorific value, J/kg

        self.R = 8.3145  # universal gas constant, J/(mol-K)
        self.A = 1.4e8  # pre-exponential factor
        self.Ea = 1.214172e5  # activation energy, J/mol
        self.nu_rxn = 1.6  # reaction order

        # self.T_in = 298  # K
        self.T_in = args.T_in  # K
        self.gradT_in = 1e5  # K/m
        phi = args.phi
        self.YF_in = phi / (phi + (2 * 32 / 16))
        self.p_in = args.p_in
        self.rho_in = self.p_in * self.W / (self.R * self.T_in)  # kg/m3

        self.phi = (2 * 32 / 16) * self.YF_in / (1 - self.YF_in)

        self.T_max = self.T_in + self.qF * self.YF_in / self.cp
        args.scales["T"] = 5.0 / self.T_max

        # ----------------------------------------------------------------------
        # load data
        # load_dir = "../ref_solution/results/p{:.2f}_phi{:.2f}/data/".format(self.p_in/101325, self.phi)
        load_dir = "../ref_solution/results/p{:.2f}_T{:.0f}_phi{:.2f}/data/".format(self.p_in/101325, self.T_in, self.phi)

        x_src = np.load(load_dir + "x.npy")
        T_src = np.load(load_dir + "T.npy")
        YF_src = np.load(load_dir + "YF.npy")
        u_src = np.load(load_dir + "u.npy")
        rho_src = np.load(load_dir + "rho.npy")
        omega_src = np.load(load_dir + "omega.npy")
        p_src = np.load(load_dir + "p.npy")
        self.sL_refe = np.load(load_dir + "sL.npy").item()

        # ----------------------------------------------------------------------
        # construct interpolation function
        self.func_T_interp = interp1d(x_src, T_src, kind="linear")
        self.func_YF_interp = interp1d(x_src, YF_src, kind="linear")
        self.func_u_interp = interp1d(x_src, u_src, kind="linear")
        self.func_rho_interp = interp1d(x_src, rho_src, kind="linear")
        self.func_omega_interp = interp1d(x_src, omega_src, kind="linear")
        self.func_p_interp = interp1d(x_src, p_src, kind="linear")

        # ----------------------------------------------------------------------
        # define ICs, BCs, OCs
        self.define_icbcocs()
        print(args)

    # ----------------------------------------------------------------------
    # reference solution with individual inputs
    def func_T(self, x):
        return self.func_T_interp(x)

    def func_YF(self, x):
        return self.func_YF_interp(x)

    def func_u(self, x):
        return self.func_u_interp(x)

    def func_rho(self, x):
        return self.func_rho_interp(x)

    def func_omega(self, x):
        return self.func_omega_interp(x)

    def func_p(self, x):
        return self.func_p_interp(x)

    # ----------------------------------------------------------------------
    # define ode
    def ode(self, x, T):
        args = self.args
        scale_T = args.scales["T"]
        scale_x = args.scales["x"]
        W, cp, qF = self.W, self.cp, self.qF
        R, A, nu_rxn = self.R, self.A, self.nu_rxn
        T_in, YF_in, rho_in = self.T_in, self.YF_in, self.rho_in
        Rg = R / W

        T_x = dde.grad.jacobian(T, x, i=0, j=0)
        T_xx = dde.grad.hessian(T, x, i=0, j=0)

        sL = self.sL_infe_s / args.scales["sL"] if "sL" in args.infer_paras else self.sL_refe
        lam = self.lam_infe_s / args.scales["lam"] if "lam" in args.infer_paras else self.lam
        Ea = self.Ea_infe_s / args.scales["Ea"] if "Ea" in args.infer_paras else self.Ea

        coef = sL + Rg * T_in / sL
        u = 0.5 * (coef - (coef ** 2 - 4 * Rg * T) ** 0.5)  # choose the smaller root (subsonic)
        rho = rho_in * sL / u
        YF = YF_in + cp * (T_in - T) / qF
        omega = A * torch.exp(-Ea / (R * T)) * (YF * rho) ** nu_rxn

        coef2 = 1 / (rho_in * sL * cp)
        energy = T_x - coef2 * lam * T_xx - coef2 * omega * qF

        energy *= (scale_T / scale_x)

        return energy

    # ----------------------------------------------------------------------
    # define ICs, BCs, OCs
    def define_icbcocs(self):
        args = self.args
        geom = self.geom
        x_l, x_r = self.x_l, self.x_r
        T_in, gradT_in = self.T_in, self.gradT_in
        scale_T = args.scales["T"]
        scale_x = args.scales["x"]

        def bdr_l(x, on_bdr):
            return on_bdr and np.isclose(x[0], x_l)

        def residual_gradTs(x, T, _):
            gradT = dde.grad.jacobian(T, x, i=0, j=0)
            return (gradT - gradT_in) * (scale_T / scale_x)  # dTs_dxs - (dTs_dxs)_in

        bc_T = ScaledDirichletBC(geom, lambda x: T_in, bdr_l, component=0, scale=scale_T)
        # bc_gradT = ScaledNeumannBC(geom, lambda x: gradT_in, bdr_l, component=0, scale=scale_T / scale_x)  # unknown bug
        bc_gradT = dde.icbc.OperatorBC(geom, residual_gradTs, bdr_l)

        if args.bc_type == "soft":
            self.icbcocs += [bc_T, bc_gradT]
            self.names["ICBCOCs"] += ["BC_T", "BC_gradT"]
        else:  # "none"
            pass

        if args.oc_type == "soft":
            n_ob = args.n_ob
            ob_x = np.linspace(x_l, x_r, n_ob)[:, None]

            ob_T = self.func_T(ob_x)
            ob_u = self.func_u(ob_x)

            normal_noise_T = np.random.randn(len(ob_T))[:, None]
            normal_noise_u = np.random.randn(len(ob_u))[:, None]
            ob_T += normal_noise_T * ob_T * args.noise_level
            ob_u += normal_noise_u * ob_u * args.noise_level

            def func_u_by_T(x, T, _):
                Rg = self.R / self.W
                sL = self.sL_infe_s / args.scales["sL"] if "sL" in args.infer_paras else self.sL_refe
                coef = sL + Rg * self.T_in / sL
                u = 0.5 * (coef - (coef ** 2 - 4 * Rg * T) ** 0.5)  # choose the smaller root (subsonic)
                return u

            oc_T = ScaledPointSetBC(ob_x, ob_T, component=0, scale=scale_T)
            oc_u = ScaledPointSetOperatorBC(ob_x, ob_u, func_u_by_T, scale=1.0)
            self.icbcocs += [oc_T]
            self.names["ICBCOCs"] += ["OC_T"]
            if args.observe_u:
                self.icbcocs += [oc_u]
                self.names["ICBCOCs"] += ["OC_u"]
        else:  # "none"
            n_ob = 0
            ob_x = np.empty([1, 1])
        self.n_ob = n_ob
