# Copyright (c) 2012-2014, GPy authors (see AUTHORS.txt).
# Licensed under the BSD 3-clause license (see LICENSE.txt)
from .parameterization.priorizable import Priorizable
from paramz import Model as ParamzModel
from paramz import transformations
import numpy as np
from ..util.linalg import pdinv

class Model(ParamzModel, Priorizable):

    def __init__(self, name):
        super(Model, self).__init__(name)  # Parameterized.__init__(self)

    def log_likelihood(self):
        raise NotImplementedError("this needs to be implemented to use the model class")

    def _log_likelihood_gradients(self):
        return self.gradient#.copy()

    def objective_function(self):
        """
        The objective function for the given algorithm.

        This function is the true objective, which wants to be minimized.
        Note that all parameters are already set and in place, so you just need
        to return the objective function here.

        For probabilistic models this is the negative log_likelihood
        (including the MAP prior), so we return it here. If your model is not
        probabilistic, just return your objective to minimize here!
        """
        return -float(self.log_likelihood()) - self.log_prior()

    def objective_function_gradients(self):
        """
        The gradients for the objective function for the given algorithm.
        The gradients are w.r.t. the *negative* objective function, as
        this framework works with *negative* log-likelihoods as a default.

        You can find the gradient for the parameters in self.gradient at all times.
        This is the place, where gradients get stored for parameters.

        This function is the true objective, which wants to be minimized.
        Note that all parameters are already set and in place, so you just need
        to return the gradient here.

        For probabilistic models this is the gradient of the negative log_likelihood
        (including the MAP prior), so we return it here. If your model is not
        probabilistic, just return your *negative* gradient here!
        """
        return -(self._log_likelihood_gradients() + self._log_prior_gradients())
        
    def CCD(self):
        """
        Code is based on implementation within GPStuff, INLA and the original Sanchez and Sanchez paper (2005)



        CCD = 	Central Composite Design, pick hyperparameters around the MAP estimate to allow us to estimate the
	        integral over them.

        Quoting https://arxiv.org/pdf/1206.5754.pdf (section 5.4) which
        describes GPStuff, which this work is based upon.
                
        "Rue et al. (2009) suggest a central composite design (CCD) for choosing
        the representative points from the posterior of the parameters with
        the aim of finding points that allow one to estimate the curvature
        of the posterior distribution around the mode. The design used here
        copies GPstuff's fractional factorial design (Sanchez and Sanchez, 2005)
        augmented with a center point and a group of star points."
        
        "The design points are all on the surface of a d-dimensional sphere
        and the star points consist of 2d points along each axis. The
        integration is then a finite sum with special weights (Vanhatalo et al.,
        2010)."
        
        Quoting that article:
        
        "The integration weights can then be determined from the statistics of a
        standard Gaussian variable,
        
        E[z^T z] = d
        E[z] = 0
        E[1] = 1
        
        where d is the dimensionality of \theta.
        sphere has radius \sqrt{d}f_0.
        The integration weights are equal for the points on the sphere.
        
        This results in integration weights,
        
        \Delta = [  (n_p-1) e^(-df_0^2/2) (f_0^2-1)  ]^{-1}
        
        n_p = number of points on sphere.
        
        f_0 > 1 is any constant (from http://www.statslab.cam.ac.uk/~rjs57/RSS/0708/Rue08.pdf, p31)
        
        
        "CCD integration speeds up the computations considerably compared to the
        grid search or Monte Carlo integration since the number of the design
        points grows very moderately."
        
        "Since CCD is based on the assumption that the posterior of the parameter
        is (close to) Gaussian, the densities at the points on the circumference
        should be monitored in order to detect serious discrepancies from this
        assumption. These densities are identical if the posterior is Gaussian
        and we have located the mode correctly, and thereby great variability on
        their values indicates that CCD has failed."
        
        TODO: From the description of GPStuff: "The posterior of the
        parameters may be far from a Gaussian distribution but for a suitable
        transformation, which is made automatically in the toolbox..." -- is
        this the same transformation we perform below?
        
        TODO: Implement the above weights in the summation.
        
        
        References:
                Sanchez, Susan M., and Paul J. Sanchez. "Very large fractional factorial and central composite designs." ACM Transactions on Modeling and Computer Simulation (TOMACS) 15.4 (2005): 362-377.
                http://calhoun.nps.edu/bitstream/handle/10945/35346/SanchezSanchezACM_TOMACS_05.pdf?sequence=1
                
                Rue, Håvard, Sara Martino, and Nicolas Chopin. "Approximate Bayesian inference for latent Gaussian models by using integrated nested Laplace approximations." Journal of the royal statistical society: Series b (statistical methodology) 71.2 (2009): 319-392.
                http://www.jstor.org/stable/40247579
                
                Vanhatalo, Jarno, Ville Pietiläinen, and Aki Vehtari. "Approximate inference for disease mapping with sparse Gaussian processes." Statistics in medicine 29.15 (2010): 1580-1607.
                http://lib.tkk.fi/Diss/2010/isbn9789526033815/article4.pdf
                
        """
        modal_params = self.optimizer_array[:].copy()
        num_free_params = modal_params.shape[0]

        # Calculate the numerical hessian for *every* parameter
        H = self.numerical_parameter_hessian()

        try:
            curv = pdinv(H)[0]
        except np.linalg.linalg.LinAlgError:
            print("Hessian is not positive definite, ensure parameters are at their MAP solution by optimizing, if that doesn't work try modifying the step length parameter")
            return
        # CCD points are calculated for unit circle, so we take the principle components and scale them such that we have a unit sphere in our parameter space, which we can then map back to parameter space
        [w, V] = np.linalg.eig(curv)
        z = (V*np.sqrt(w[None,:])).T

        # Calculate points, we will do just CCD for now

        # First we build the hadamarx matrix from the paper that encodes *all* the points possible in the num_free_params dimensional space.
        grow = np.sum(num_free_params >= np.array([1, 2, 3, 4, 6, 7, 9, 12, 18, 22, 30, 39, 53, 70, 93]))

        H0 = 1

        #Build the design matrix that holds *every* corner on the num_free_params dimensional hyper-cube (the hadamard matrix), H in the paper
        for i in range(grow):
            H0 = np.vstack([np.hstack([H0, H0]), 
                            np.hstack([H0, -H0])])
        
        # For each additional parameter our dimensional space increases, as does the number of CCD points we require to do the approximate integration. CCD is a method of deciding which points should be included and which can be ignored without a significant effect. It would be ideal but too computationally expensive - when we have many parameters - to use all the corners of the hypercube in our integration
        # See Sanchez and Sanchez (2005) for details. on how the points are chosen
        walsh_inds = np.array([1, 2, 4, 8, 15, 16, 32, 51, 64, 85, 106, 128, 150, 171, 219, 237, 247, 256, 279, 297, 455, 512, 537, 557, 597, 643, 803, 863, 898, 1024, 1051, 1070, 1112, 1169, 1333, 1345, 1620, 1866, 2048, 2076, 2085, 2158, 2372, 2456, 2618, 2800, 2873, 3127, 3284, 3483, 3557, 3763, 4096, 4125, 4135, 4176, 4435, 4459, 4469, 4497, 4752, 5255, 5732, 5801, 5915, 6100, 6369, 6907, 7069, 8192, 8263, 8351, 8422, 8458, 8571, 8750, 8858, 9124, 9314, 9500, 10026, 10455, 10556, 11778, 11885, 11984, 13548, 14007, 14514, 14965, 15125, 15554, 16384, 16457, 16517, 16609, 16771, 16853, 17022, 17453, 17891, 18073, 18562, 18980, 19030, 19932, 20075, 20745, 21544, 22633, 23200, 24167, 25700, 26360, 26591, 26776, 28443, 28905, 29577, 32705])

        used_walsh_inds = walsh_inds[:num_free_params]

        # For every additional parameters, we get an additional number of points (which as we decide to drop points using Sanchezes rules, won't necessarily grow at the same rate)
        ccd_points = H0[:, used_walsh_inds]
        
        # ccd only gives corner points, so lets add an additional one for the centre point
        ccd_points = np.vstack([np.zeros((1,num_free_params)), ccd_points])

        # Now we add the points on the unit hyper-sphere that arent on the corners
        for i in range(num_free_params):
            top_edge = np.zeros(num_free_params)
            top_edge[i] = np.sqrt(num_free_params)
            bottom_edge = np.zeros(num_free_params)
            bottom_edge[i] = -np.sqrt(num_free_params)
            ccd_points = np.vstack([ccd_points, top_edge, bottom_edge])

        # Find the appropriate scaling such that the edges lie on a boundary of equal density
        # First find the density at the mode
        log_marginal = lambda p: -self._objective(p)
        mode_density = log_marginal(modal_params)  # FIXME: We really want to evaluate the log_marginal not the negative objective as it makes more sense, but they are equivalent in this case

        # Treating the posterior over hyperparameters as a standard normal, moving z*sqrt(2) should make the likelihood drop by 1, as z should be acting as a unit vector along the principle components of the posterior. 

        scalings = np.ones_like(ccd_points)        
        # The below code makes me feel dirty. Pythonise it!
        # This is naive scaling, assuming that it is well approximated by a standard normal, in practice you might want to scale in different directions seperately (split normal approximation)
        for j in range(num_free_params*2):
            temp = np.zeros((1, num_free_params))
            if j % 2:
                direction = -1
            else:
                direction = 1
            ind = int(np.ceil(j // 2))  # This integer division is required, will not work with python 3
            temp[0, ind] = direction
            
            # This is the point mapped onto the contour of a unit gaussian, stretched by the eigenvectors over the marginal likelihood

            contour_point = modal_params + np.sqrt(2)*temp.dot(z)
            point_density = log_marginal(contour_point)

            if mode_density > point_density:
                # The scale is based on the amount this point must be scaled in order to be on the contour of the stretched standard normal (stretched by principle components)
                scale = np.sqrt(1.0 / (mode_density - point_density))
            else:
                print("Contour point is higher than mode, must be multimodal or mode not found")
                scale = 1  # Print a warning and say "dont scale in this direction"

            # Set the scaling for all dimensions where the point is in this direction, when looking at this dimension, clipped.
            scalings[ccd_points[:, ind]*direction > 0.0, ind] = np.maximum(np.minimum(scale, 10.0), 1/10.0)
            
        # Make the points *slightly* further out. Aki Vehtari has shown this to be more accurate
        over_scale = 1.1
        ccd_points = over_scale*scalings*ccd_points

        # We have the scaled ccd_points, now we need to map them into parameter space by projecting along principle components and shifting to mode
        param_points = ccd_points.dot(z) + modal_params

        # Evaluate log marginal at all parameter points
        point_densities = np.ones(param_points.shape[0])*np.nan
        for point_ind, param_point in enumerate(param_points):
            point_densities[point_ind] = log_marginal(param_point)

        # Remove nan densities
        non_nan_densities = np.isfinite(point_densities).flatten()
        point_densities = point_densities[non_nan_densities]
        param_points = param_points[non_nan_densities, :]

        point_densities = point_densities - np.max(point_densities)  # Why don't we have to deal with this shift?
        point_densities = np.exp(point_densities)

        if num_free_params > 0:
            # Each section that the ccd point represents needs an associated weight. Since every point is on the same contour, all weights are the same, except the centre weight
            point_weights = 1.0/((2.0*np.pi)**(-num_free_params*0.5) * np.exp(-0.5*num_free_params*over_scale**2) * (param_points.shape[0]-1)*over_scale**2)
            centre_weight = (2*np.pi)**(num_free_params*0.5) * (1 - 1.0/over_scale**2)
            # normalize
            point_weights = point_weights / centre_weight
            centre_weight = 1.0
            point_densities[1:] *= point_weights

        # Normalize density
        point_densities /= point_densities.sum()

        # Remove small density points
        non_small_densities = (point_densities > 0.01/point_densities.size).flatten()
        point_densities = point_densities[non_small_densities]
        param_points = param_points[non_small_densities, :]
        point_densities /= point_densities.sum()
        
        
        #Mike's temporary attempt to calculate point_densities
        #TODO

        transformed_points = param_points.copy()
        #alan's original code to transform those parameters, to the true space of parameters again
        #mike's change: some parameters have no transform, and thus won't be called by any of the
        #iterations through m2.constraints.items(). To handle these we keep track of those parameters
        #not included, and then add them (untransformed) at the end.
        f = np.ones(self.size).astype(bool)
        f[self.constraints[transformations.__fixed__]] = transformations.FIXED
        #TODO Check: Presumably only one constraint applies to each parameter?
        new_t_points = [] 
        todo = list(range(0,sum(f)))
        new_t_points = np.zeros_like(transformed_points)
        for c, ind in self.constraints.items():
            if c != transformations.__fixed__:
                for i in ind[f[ind]]:
                    z[:,i] = c.f(z[:,i])
                    new_t_points[:,i] = (c.f(transformed_points[:, i]))
                    todo.remove(i)

        for i in todo:
            new_t_points[:,i] = transformed_points[:, i]
            
            
        return new_t_points, point_densities, scalings, z

    def numerical_parameter_hessian(self, step_length=1e-3):
        """
        Calculate the numerical hessian for the posterior of the parameters

        Often this will want to be done at the modal values of the parameters so the optimizer should be called first
        
        H = [d^2L_dp1dp1, d^2L_dp1dp2, d^2L_dp1dp3, ...;
             d^2L_dp2dp1, d^2L_dp2dp2, d^2L_dp3dp3, ...;
            ...
             ]
        Where the vector p is all of the parameters in param_array, not just parameters of this model class itself
        """
        # We use optimizer array as we want to ignore any parameters with fixed values
        num_child_params = self.optimizer_array.shape[0]
        
        H = np.eye(num_child_params)

        initial_params = self.optimizer_array[:].copy()
        # Calculate gradient of marginal likelihood moving one at a time
        for i in range(num_child_params):
            # Pick out the e vector with one in the parameter that we wish to move
            e = H[:,i]
            # Get parameters required to evaluate by nudging left and right
            left = initial_params - step_length*e
            right = initial_params + step_length*e

            left_grad = self._grads(left)
            right_grad = self._grads(right)

            finite_diff = (right_grad - left_grad) / (2*step_length)

            #Put in the correct row of the hessian
            H[:,i] = finite_diff

        return H        
