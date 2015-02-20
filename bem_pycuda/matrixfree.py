'''
  Copyright (C) 2013 by Christopher Cooper, Lorena Barba

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in
  all copies or substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
  THE SOFTWARE.
'''

from numpy import *
import sys
sys.path.append('tree')
from FMMutils import *
from projection import project, project_Kt, project_Ktqual, get_phir, get_phir_gpu, getWeights
from classes import parameters, index_constant
import time
sys.path.append('../util')
from semi_analytical import GQ_1D
from direct import coulomb_direct

# PyCUDA libraries
import pycuda.autoinit
import pycuda.gpuarray as gpuarray

## Note: 
##  Remember ordering of equations:
##      Same order as defined in config file,
##      with internal equation first and the external equation.

def selfInterior(surf, s, LorY, param, ind0, timing, kernel):
#    print 'SELF INTERIOR, surface: %i'%s
    K_diag = 2*pi
    V_diag = 0
    IorE   = 1
    K_lyr, V_lyr = project(surf.XinK, surf.XinV, LorY, surf, surf,
                            K_diag, V_diag, IorE, s, param, ind0, timing, kernel)
    v = K_lyr - V_lyr
    return v

def selfExterior(surf, s, LorY, param, ind0, timing, kernel):
#    print 'SELF EXTERIOR, surface: %i, E_hat: %f'%(s, surf.E_hat)
    K_diag = -2*pi
    V_diag = 0.
    IorE   = 2
    K_lyr, V_lyr = project(surf.XinK, surf.XinV, LorY, surf, surf, 
                            K_diag, V_diag, IorE, s, param, ind0, timing, kernel)
    v = -K_lyr + surf.E_hat*V_lyr
    return v, K_lyr, V_lyr

def nonselfExterior(surf, src, tar, LorY, param, ind0, timing, kernel):
#    print 'NONSELF EXTERIOR, source: %i, target: %i, E_hat: %f'%(src,tar, surf[src].E_hat)
    K_diag = 0
    V_diag = 0
    IorE   = 1
    K_lyr, V_lyr = project(surf[src].XinK, surf[src].XinV, LorY, surf[src], surf[tar], 
                            K_diag, V_diag, IorE, src, param, ind0, timing, kernel)
    v = -K_lyr + surf[src].E_hat*V_lyr
    return v

def nonselfInterior(surf, src, tar, LorY, param, ind0, timing, kernel):
#    print 'NONSELF INTERIOR, source: %i, target: %i'%(src,tar)
    K_diag = 0
    V_diag = 0
    IorE   = 2
    K_lyr, V_lyr = project(surf[src].XinK, surf[src].XinV, LorY, surf[src], surf[tar], 
                            K_diag, V_diag, IorE, src, param, ind0, timing, kernel)
    v = K_lyr - V_lyr
    return v

def selfASC(surf, src, tar, LorY, param, ind0, timing, kernel):

    Kt_diag = -2*pi * (surf.Eout+surf.Ein)/(surf.Eout-surf.Ein)
    V_diag = 0
    
    Kt_lyr = project_Kt(surf.XinK, LorY, surf, surf, 
                            Kt_diag, src, param, ind0, timing, kernel)
    
    v = -Kt_lyr
    return v

def selfASCqual(surf, src, tar, LorY, param, ind0, timing, kernel):

    Kt_diag = -2*pi * (surf.Eout+surf.Ein)/(surf.Eout-surf.Ein) * surf.Area
    V_diag = 0
    
    Kt_lyr = project_Ktqual(surf.XinK, LorY, surf, surf, 
                            Kt_diag, src, param, ind0, timing, kernel)
    
    v = -Kt_lyr
    return v

def gmres_dot (X, surf_array, field_array, ind0, param, timing, kernel):
    
    Nfield = len(field_array)
    Nsurf = len(surf_array)

#   Place weights on corresponding surfaces and allocate memory
    Naux = 0
    for i in range(Nsurf):
        N = len(surf_array[i].triangle)
        if surf_array[i].surf_type=='dirichlet_surface':
            surf_array[i].XinK = zeros(N) 
            surf_array[i].XinV = X[Naux:Naux+N] 
            Naux += N
        elif surf_array[i].surf_type=='neumann_surface' or surf_array[i].surf_type=='asc_surface':
            surf_array[i].XinK = X[Naux:Naux+N] 
            surf_array[i].XinV = zeros(N)
            Naux += N
        else:
            surf_array[i].XinK     = X[Naux:Naux+N]
            surf_array[i].XinV     = X[Naux+N:Naux+2*N]
            Naux += 2*N 

        surf_array[i].Xout_int = zeros(N) 
        surf_array[i].Xout_ext = zeros(N)

#   Loop over fields
    for F in range(Nfield):

        parent_type = 'no_parent'
        if len(field_array[F].parent)>0:
            parent_type = surf_array[field_array[F].parent[0]].surf_type

        if parent_type=='asc_surface':
#           ASC only for self-interaction so far 
            LorY = field_array[F].LorY
            p = field_array[F].parent[0]
            if param.linearSys == 'collocation':
                v = selfASC(surf_array[p], p, p, LorY, param, ind0, timing, kernel)
            elif param.linearSys == 'qualocation':
                v = selfASCqual(surf_array[p], p, p, LorY, param, ind0, timing, kernel)

            surf_array[p].Xout_int += v

        if parent_type!='dirichlet_surface' and parent_type!='neumann_surface' and parent_type!='asc_surface':
            LorY = field_array[F].LorY
            param.kappa = field_array[F].kappa
#           print '\n---------------------'
#           print 'REGION %i, LorY: %i, kappa: %f'%(F,LorY,param.kappa)

#           if parent surface -> self interior operator
            if len(field_array[F].parent)>0:
                p = field_array[F].parent[0]
                v = selfInterior(surf_array[p], p, LorY, param, ind0, timing, kernel)
                surf_array[p].Xout_int += v
                
#           if child surface -> self exterior operator + sibling interaction
#           sibling interaction: non-self exterior saved on exterior vector
            if len(field_array[F].child)>0:
                C = field_array[F].child
                for c1 in C:
                    if surf_array[c1].surf_type!='asc_surface':
                        v,t1,t2 = selfExterior(surf_array[c1], c1, LorY, param, ind0, timing, kernel)
                        surf_array[c1].Xout_ext += v
                    for c2 in C:
                        if c1!=c2:
                            v = nonselfExterior(surf_array, c2, c1, LorY, param, ind0, timing, kernel)
                            surf_array[c1].Xout_ext += v

#           if child and parent surface -> parent-child and child-parent interaction
#           parent->child: non-self interior saved on exterior vector 
#           child->parent: non-self exterior saved on interior vector
            if len(field_array[F].child)>0 and len(field_array[F].parent)>0:
                p = field_array[F].parent[0]
                C = field_array[F].child
                for c in C:
                    v = nonselfExterior(surf_array, c, p, LorY, param, ind0, timing, kernel)
                    surf_array[p].Xout_int += v
                    v = nonselfInterior(surf_array, p, c, LorY, param, ind0, timing, kernel)
                    surf_array[c].Xout_ext += v
     
#   Gather results into the result vector
    MV = zeros(len(X))
    Naux = 0
    for i in range(Nsurf):
        N = len(surf_array[i].triangle)
        if surf_array[i].surf_type=='dirichlet_surface':
            MV[Naux:Naux+N]     = surf_array[i].Xout_ext*surf_array[i].Precond[0,:] 
            Naux += N
        elif surf_array[i].surf_type=='neumann_surface':
            MV[Naux:Naux+N]     = surf_array[i].Xout_ext*surf_array[i].Precond[0,:] 
            Naux += N
        elif surf_array[i].surf_type=='asc_surface':
            MV[Naux:Naux+N]     = surf_array[i].Xout_int*surf_array[i].Precond[0,:] 
            Naux += N
        else:
            MV[Naux:Naux+N]     = surf_array[i].Xout_int*surf_array[i].Precond[0,:] + surf_array[i].Xout_ext*surf_array[i].Precond[1,:] 
            MV[Naux+N:Naux+2*N] = surf_array[i].Xout_int*surf_array[i].Precond[2,:] + surf_array[i].Xout_ext*surf_array[i].Precond[3,:] 
            Naux += 2*N

    return MV

def generateRHS(field_array, surf_array, param, kernel, timing, ind0):
    F = zeros(param.Neq)
    w = getWeights(param.K)

#   Point charge contribution to RHS
    for j in range(len(field_array)):
        Nq = len(field_array[j].q)
        if Nq>0:
#           First look at CHILD surfaces
            for s in field_array[j].child:          # Loop over surfaces
#           Locate position of surface s in RHS
                s_start = 0
                for ss in range(s):
                    if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[ss].surf_type=='asc_surface':
                        s_start += len(surf_array[ss].triangle)
                    else:
                        s_start += 2*len(surf_array[ss].triangle)

                s_size = len(surf_array[s].triangle)

                aux = zeros(len(surf_array[s].triangle))
                for i in range(Nq):
                    dx_pq = surf_array[s].xi - field_array[j].xq[i,0] 
                    dy_pq = surf_array[s].yi - field_array[j].xq[i,1]
                    dz_pq = surf_array[s].zi - field_array[j].xq[i,2]
                    R_pq = sqrt(dx_pq*dx_pq + dy_pq*dy_pq + dz_pq*dz_pq)

                    if surf_array[s].surf_type=='asc_surface':

                        if param.linearSys == 'collocation':
                            aux -= field_array[j].q[i]/(R_pq*R_pq*R_pq) * (dx_pq*surf_array[s].normal[:,0] \
                                                                        + dy_pq*surf_array[s].normal[:,1] \
                                                                        + dz_pq*surf_array[s].normal[:,2])
                        elif param.linearSys == 'qualocation':

                            aux2 = -field_array[j].q[i]/(R_pq*R_pq*R_pq)

                            aux_x = aux2*dx_pq
                            aux_y = aux2*dy_pq
                            aux_z = aux2*dz_pq

                            aux_x = reshape(aux_x,(len(surf_array[s].triangle), param.K))
                            aux_y = reshape(aux_y,(len(surf_array[s].triangle), param.K))
                            aux_z = reshape(aux_z,(len(surf_array[s].triangle), param.K))

                            R_pq = reshape(R_pq,(len(surf_array[s].triangle), param.K))

                            far_indices = where(sqrt(2*surf_array[s].Area)/R_pq[:,0] < param.threshold)
                            close_indices = where(sqrt(2*surf_array[s].Area)/R_pq[:,0] > param.threshold)

#                           If close, I'll use only one Gauss point. For some reason this works better.
#                           Need to check this

                            aux_x2 = zeros(len(surf_array[s].triangle))
                            aux_y2 = zeros(len(surf_array[s].triangle))
                            aux_z2 = zeros(len(surf_array[s].triangle))

                            aux_x2[far_indices] = sum(aux_x[far_indices]*w, axis=1)
                            aux_y2[far_indices] = sum(aux_y[far_indices]*w, axis=1)
                            aux_z2[far_indices] = sum(aux_z[far_indices]*w, axis=1)
                            
                            w_close = zeros(param.K)
                            w_close[0] = 1.
                            aux_x2[close_indices] = sum(aux_x[close_indices]*w_close, axis=1)
                            aux_y2[close_indices] = sum(aux_y[close_indices]*w_close, axis=1)
                            aux_z2[close_indices] = sum(aux_z[close_indices]*w_close, axis=1)

                            aux += surf_array[s].Area * (aux_x2*surf_array[s].normal[:,0] \
                                                    + aux_y2*surf_array[s].normal[:,1] \
                                                    + aux_z2*surf_array[s].normal[:,2])

                    else:
                        aux += field_array[j].q[i]/(field_array[j].E*R_pq)

#               For CHILD surfaces, q contributes to RHS in 
#               EXTERIOR equation (hence Precond[1,:] and [3,:])
    
#               No preconditioner
#                F[s_start:s_start+s_size] += aux

#               With preconditioner
#               If surface is dirichlet or neumann it has only one equation, affected by Precond[0,:]
#               We do this only here (and not in the parent case) because interaction of charges 
#               with dirichlet or neumann surface happens only for the surface as a child surfaces.
                if surf_array[s].surf_type=='dirichlet_surface' or surf_array[s].surf_type=='neumann_surface' or  surf_array[s].surf_type=='asc_surface':
                    F[s_start:s_start+s_size] += aux*surf_array[s].Precond[0,:]
                else:
                    F[s_start:s_start+s_size] += aux*surf_array[s].Precond[1,:]
                    F[s_start+s_size:s_start+2*s_size] += aux*surf_array[s].Precond[3,:]

#           Now look at PARENT surface
            if len(field_array[j].parent)>0:
#           Locate position of surface s in RHS
                s = field_array[j].parent[0]
                s_start = 0
                for ss in range(s):
                    if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[ss].surf_type=='asc_surface':
                        s_start += len(surf_array[ss].triangle)
                    else:
                        s_start += 2*len(surf_array[ss].triangle)

                s_size = len(surf_array[s].triangle)

                aux = zeros(len(surf_array[s].triangle))
                for i in range(Nq):
                    dx_pq = surf_array[s].xi - field_array[j].xq[i,0] 
                    dy_pq = surf_array[s].yi - field_array[j].xq[i,1]
                    dz_pq = surf_array[s].zi - field_array[j].xq[i,2]
                    R_pq = sqrt(dx_pq*dx_pq + dy_pq*dy_pq + dz_pq*dz_pq)

                    if surf_array[s].surf_type=='asc_surface':

                        if param.linearSys == 'collocation':
                            aux -= field_array[j].q[i]/(R_pq*R_pq*R_pq) * (dx_pq*surf_array[s].normal[:,0] \
                                                                        + dy_pq*surf_array[s].normal[:,1] \
                                                                        + dz_pq*surf_array[s].normal[:,2])
                        elif param.linearSys == 'qualocation':

                            aux2 = -field_array[j].q[i]/(R_pq*R_pq*R_pq)

                            aux_x = aux2*dx_pq
                            aux_y = aux2*dy_pq
                            aux_z = aux2*dz_pq

                            aux_x = reshape(aux_x,(len(surf_array[s].triangle), param.K))
                            aux_y = reshape(aux_y,(len(surf_array[s].triangle), param.K))
                            aux_z = reshape(aux_z,(len(surf_array[s].triangle), param.K))
                            R_pq = reshape(R_pq,(len(surf_array[s].triangle), param.K))

                            far_indices = where(sqrt(2*surf_array[s].Area)/R_pq[:,0] < param.threshold)
                            close_indices = where(sqrt(2*surf_array[s].Area)/R_pq[:,0] > param.threshold)

#                           If close, I'll use only one Gauss point. For some reason this works better.
#                           Need to check this

                            aux_x2 = zeros(len(surf_array[s].triangle))
                            aux_y2 = zeros(len(surf_array[s].triangle))
                            aux_z2 = zeros(len(surf_array[s].triangle))

                            aux_x2[far_indices] = sum(aux_x[far_indices]*w, axis=1)
                            aux_y2[far_indices] = sum(aux_y[far_indices]*w, axis=1)
                            aux_z2[far_indices] = sum(aux_z[far_indices]*w, axis=1)
                            
                            w_close = zeros(param.K)
                            w_close[0] = 1.
                            aux_x2[close_indices] = sum(aux_x[close_indices]*w_close, axis=1)
                            aux_y2[close_indices] = sum(aux_y[close_indices]*w_close, axis=1)
                            aux_z2[close_indices] = sum(aux_z[close_indices]*w_close, axis=1)

                            aux += surf_array[s].Area * (aux_x2*surf_array[s].normal[:,0] \
                                                    + aux_y2*surf_array[s].normal[:,1] \
                                                    + aux_z2*surf_array[s].normal[:,2])

                    else:
                        aux += field_array[j].q[i]/(field_array[j].E*R_pq)

#               No preconditioner
#                F[s_start:s_start+s_size] += aux
#               With preconditioner
                if surf_array[s].surf_type=='asc_surface':
                    F[s_start:s_start+s_size] += aux*surf_array[s].Precond[0,:]
                else:
                    F[s_start:s_start+s_size] += aux*surf_array[s].Precond[0,:]
                    F[s_start+s_size:s_start+2*s_size] += aux*surf_array[s].Precond[2,:]

#   Dirichlet/Neumann contribution to RHS
    for j in range(len(field_array)):

        dirichlet = []
        neumann   = []
        LorY = field_array[j].LorY

#       Find Dirichlet and Neumann surfaces in region
#       Dirichlet/Neumann surfaces can only be child of region,
#       no point on looking at parent surface
        for s in field_array[j].child:
            if surf_array[s].surf_type=='dirichlet_surface':
                dirichlet.append(s)
            elif surf_array[s].surf_type=='neumann_surface':
                neumann.append(s)
    
        if len(neumann)>0 or len(dirichlet)>0:
            
#           First look at influence on SIBLING surfaces
            for s in field_array[j].child:

                param.kappa = field_array[j].kappa

#               Effect of dirichlet surfaces
                for sd in dirichlet:
                    K_diag = -2*pi*(sd==s)
                    V_diag = 0
                    IorE   = 2
                    K_lyr, V_lyr = project(surf_array[sd].phi0, zeros(len(surf_array[sd].xi)), LorY, surf_array[sd], 
                            surf_array[s], K_diag, V_diag, IorE, sd, param, ind0, timing, kernel)

#                   Find location of surface s in RHS array
                    s_start = 0
                    for ss in range(s):
                        if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                            s_start += len(surf_array[ss].triangle)
                        else:
                            s_start += 2*len(surf_array[ss].triangle)

                    s_size = len(surf_array[s].triangle)

#                   if s is a charged surface, the surface has only one equation, 
#                   else, s has 2 equations and K_lyr affects the external
#                   equation (SIBLING surfaces), which is placed after the internal 
#                   one, hence Precond[1,:] and Precond[3,:].
                    if surf_array[s].surf_type=='dirichlet_surface' or surf_array[s].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                        F[s_start:s_start+s_size] += K_lyr * surf_array[s].Precond[0,:]
                    else:
                        F[s_start:s_start+s_size] += K_lyr * surf_array[s].Precond[1,:]
                        F[s_start+s_size:s_start+2*s_size] += K_lyr * surf_array[s].Precond[3,:]

#               Effect of neumann surfaces
                for sn in neumann:
                    K_diag = 0
                    V_diag = 0
                    IorE   = 2
                    K_lyr, V_lyr = project(zeros(len(surf_array[sn].phi0)), surf_array[sn].phi0, LorY, surf_array[sn], 
                            surf_array[s], K_diag, V_diag, IorE, sn, param, ind0, timing, kernel)

#                   Find location of surface s in RHS array
                    s_start = 0
                    for ss in range(s):
                        if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                            s_start += len(surf_array[ss].triangle)
                        else:
                            s_start += 2*len(surf_array[ss].triangle)

                    s_size = len(surf_array[s].triangle)

#                   if s is a charge surface, the surface has only one equation, 
#                   else, s has 2 equations and V_lyr affects the external
#                   equation, which is placed after the internal one, hence
#                   Precond[1,:] and Precond[3,:].
                    if surf_array[s].surf_type=='dirichlet_surface' or surf_array[s].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                        F[s_start:s_start+s_size] += -V_lyr * surf_array[s].Precond[0,:]
                    else:
                        F[s_start:s_start+s_size] += -V_lyr * surf_array[s].Precond[1,:]
                        F[s_start+s_size:s_start+2*s_size] += -V_lyr * surf_array[s].Precond[3,:]

#           Now look at influence on PARENT surface
#           The dirichlet/neumann surface will never be the parent, 
#           since we are not solving for anything inside them.
#           Then, in this case we will not look at self interaction,
#           which is dealt with by the sibling surface section
            if len(field_array[j].parent)==1:

                s = field_array[j].parent[0]


#               Effect of dirichlet surfaces
                for sd in dirichlet:
                    K_diag = 0  
                    V_diag = 0
                    IorE   = 2
                    K_lyr, V_lyr = project(surf_array[sd].phi0, zeros(len(surf_array[sd].xi)), LorY, surf_array[sd], 
                            surf_array[s], K_diag, V_diag, IorE, sd, param, ind0, timing, kernel)

#                   Find location of surface s in RHS array
                    s_start = 0
                    for ss in range(s):
                        if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                            s_start += len(surf_array[ss].triangle)
                        else:
                            s_start += 2*len(surf_array[ss].triangle)

                    s_size = len(surf_array[s].triangle)

#                   Surface s has 2 equations and K_lyr affects the internal
#                   equation, hence Precond[0,:] and Precond[2,:].
                    F[s_start:s_start+s_size] += K_lyr * surf_array[s].Precond[0,:]
                    F[s_start+s_size:s_start+2*s_size] += K_lyr * surf_array[s].Precond[2,:]

#               Effect of neumann surfaces
                for sn in neumann:
                    K_diag = 0
                    V_diag = 0
                    IorE   = 2
                    K_lyr, V_lyr = project(zeros(len(surf_array[sn].phi0)), surf_array[sn].phi0, LorY, surf_array[sn], 
                            surf_array[s], K_diag, V_diag, IorE, sn, param, ind0, timing, kernel)

#                   Find location of surface s in RHS array
                    s_start = 0
                    for ss in range(s):
                        if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                            s_start += len(surf_array[ss].triangle)
                        else:
                            s_start += 2*len(surf_array[ss].triangle)

                    s_size = len(surf_array[s].triangle)

#                   Surface s has 2 equations and K_lyr affects the internal
#                   equation, hence Precond[0,:] and Precond[2,:].
                    F[s_start:s_start+s_size] += -V_lyr * surf_array[s].Precond[0,:]
                    F[s_start+s_size:s_start+2*s_size] += -V_lyr * surf_array[s].Precond[2,:]

    return F

def generateRHS_gpu(field_array, surf_array, param, kernel, timing, ind0):

    F = zeros(param.Neq)
    w = getWeights(param.K)
    REAL = param.REAL
    computeRHS_gpu = kernel.get_function("compute_RHS")
    computeRHSKt_gpu = kernel.get_function("compute_RHSKt")
    computeRHSKtqual_gpu = kernel.get_function("compute_RHSKtqual")
    for j in range(len(field_array)):
        Nq = len(field_array[j].q)
        if Nq>0:
        
#           First for CHILD surfaces
            for s in field_array[j].child[:]:       # Loop over surfaces
#           Locate position of surface s in RHS
                s_start = 0
                for ss in range(s):
                    if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[ss].surf_type=='asc_surface':
                        s_start += len(surf_array[ss].triangle)
                    else:
                        s_start += 2*len(surf_array[ss].triangle)

                s_size = len(surf_array[s].triangle)
                Nround = len(surf_array[s].twig)*param.NCRIT
                NCRIT = param.NCRIT

                if param.linearSys=='qualocation':
                    Nround *= param.K
                    NCRIT *= param.K

                GSZ = int(ceil(float(Nround)/NCRIT)) # CUDA grid size

                if surf_array[s].surf_type!='asc_surface':
                    F_gpu = gpuarray.zeros(Nround, dtype=REAL)     
                    computeRHS_gpu(F_gpu, field_array[j].xq_gpu, field_array[j].yq_gpu, field_array[j].zq_gpu, field_array[j].q_gpu,
                                surf_array[s].xiDev, surf_array[s].yiDev, surf_array[s].ziDev, surf_array[s].sizeTarDev, int32(Nq), 
                                REAL(field_array[j].E), int32(param.NCRIT), int32(param.BlocksPerTwig), block=(param.BSZ,1,1), grid=(GSZ,1)) 

                    aux = zeros(Nround)
                    F_gpu.get(aux)

                else: 
                    Fx_gpu = gpuarray.zeros(Nround, dtype=REAL)     
                    Fy_gpu = gpuarray.zeros(Nround, dtype=REAL)     
                    Fz_gpu = gpuarray.zeros(Nround, dtype=REAL)     
                    if param.linearSys=='collocation':
                        computeRHSKt_gpu(Fx_gpu, Fy_gpu, Fz_gpu, field_array[j].xq_gpu, field_array[j].yq_gpu, field_array[j].zq_gpu, field_array[j].q_gpu,
                                    surf_array[s].xiDev, surf_array[s].yiDev, surf_array[s].ziDev, surf_array[s].sizeTarDev, int32(Nq), 
                                    REAL(field_array[j].E), int32(NCRIT), int32(param.BlocksPerTwig), block=(param.BSZ,1,1), grid=(GSZ,1)) 
                        aux_x = zeros(Nround)
                        aux_y = zeros(Nround)
                        aux_z = zeros(Nround)
                        Fx_gpu.get(aux_x)
                        Fy_gpu.get(aux_y)
                        Fz_gpu.get(aux_z)

                        aux = aux_x[surf_array[s].unsort]*surf_array[s].normal[:,0] + \
                              aux_y[surf_array[s].unsort]*surf_array[s].normal[:,1] + \
                              aux_z[surf_array[s].unsort]*surf_array[s].normal[:,2]

                    elif param.linearSys=='qualocation':
                        computeRHSKtqual_gpu(Fx_gpu, Fy_gpu, Fz_gpu, field_array[j].xq_gpu, field_array[j].yq_gpu, field_array[j].zq_gpu, field_array[j].q_gpu,
                                    surf_array[s].xiDev, surf_array[s].yiDev, surf_array[s].ziDev, surf_array[s].vertexDev, surf_array[s].AreaDev, 
                                    surf_array[s].sizeTarDev, surf_array[s].kDev, surf_array[s].XskDev, surf_array[s].WskDev, int32(Nq), int32(param.K_fine),
                                    REAL(param.threshold), REAL(w[0]), int32(NCRIT), int32(param.BlocksPerTwig), block=(param.BSZ,1,1), grid=(GSZ,1)) 

                        aux_x = zeros(Nround)
                        aux_y = zeros(Nround)
                        aux_z = zeros(Nround)
                        Fx_gpu.get(aux_x)
                        Fy_gpu.get(aux_y)
                        Fz_gpu.get(aux_z)

                        aux_x = aux_x[surf_array[s].unsort]
                        aux_y = aux_y[surf_array[s].unsort]
                        aux_z = aux_z[surf_array[s].unsort]

                        aux_x = reshape(aux_x, (len(surf_array[s].triangle),param.K))
                        aux_y = reshape(aux_y, (len(surf_array[s].triangle),param.K))
                        aux_z = reshape(aux_z, (len(surf_array[s].triangle),param.K))

                        aux = surf_array[s].Area * (sum(aux_x*w,axis=1)*surf_array[s].normal[:,0] + \
                                                    sum(aux_y*w,axis=1)*surf_array[s].normal[:,1] + \
                                                    sum(aux_z*w,axis=1)*surf_array[s].normal[:,2]) 


#               For CHILD surfaces, q contributes to RHS in 
#               EXTERIOR equation (hence Precond[1,:] and [3,:])
    
#               No preconditioner
#                F[s_start:s_start+s_size] += aux
#               With preconditioner
#                F[s_start:s_start+s_size] += aux[surf_array[s].unsort]*surf_array[s].Precond[1,:]
#               F[s_start+s_size:s_start+2*s_size] += aux[surf_array[s].unsort]*surf_array[s].Precond[3,:]

#               With preconditioner
#               If surface is dirichlet or neumann it has only one equation, affected by Precond[0,:]
#               We do this only here (and not in the parent case) because interaction of charges 
#               with dirichlet or neumann surface happens only for the surface as a child surfaces.
                if surf_array[s].surf_type=='dirichlet_surface' or surf_array[s].surf_type=='neumann_surface':
                    F[s_start:s_start+s_size] += aux[surf_array[s].unsort]*surf_array[s].Precond[0,:]
                elif surf_array[s].surf_type=='asc_surface':
                    F[s_start:s_start+s_size] += aux*surf_array[s].Precond[0,:]
                else:
                    F[s_start:s_start+s_size] += aux[surf_array[s].unsort]*surf_array[s].Precond[1,:]
                    F[s_start+s_size:s_start+2*s_size] += aux[surf_array[s].unsort]*surf_array[s].Precond[3,:]


#           Now for PARENT surface
            if len(field_array[j].parent)>0:
                s = field_array[j].parent[0]

#           Locate position of surface s in RHS
                s_start = 0
                for ss in range(s):
                    if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[ss].surf_type=='asc_surface':
                        s_start += len(surf_array[ss].triangle)
                    else:
                        s_start += 2*len(surf_array[ss].triangle)

                s_size = len(surf_array[s].triangle)
                Nround = len(surf_array[s].twig)*param.NCRIT

                NCRIT = param.NCRIT
                
                if param.linearSys=='qualocation':
                    Nround *= param.K
                    NCRIT *= param.K

                GSZ = int(ceil(float(Nround)/NCRIT)) # CUDA grid size

                if surf_array[s].surf_type!='asc_surface':
                    F_gpu = gpuarray.zeros(Nround, dtype=REAL)     
                    computeRHS_gpu(F_gpu, field_array[j].xq_gpu, field_array[j].yq_gpu, field_array[j].zq_gpu, field_array[j].q_gpu,
                                surf_array[s].xiDev, surf_array[s].yiDev, surf_array[s].ziDev, surf_array[s].sizeTarDev, int32(Nq), 
                                REAL(field_array[j].E), int32(NCRIT), int32(param.BlocksPerTwig), block=(param.BSZ,1,1), grid=(GSZ,1)) 

                    aux = zeros(Nround)
                    F_gpu.get(aux)

                else:
                    Fx_gpu = gpuarray.zeros(Nround, dtype=REAL)     
                    Fy_gpu = gpuarray.zeros(Nround, dtype=REAL)     
                    Fz_gpu = gpuarray.zeros(Nround, dtype=REAL)     

                    if param.linearSys=='collocation':
                        computeRHSKt_gpu(Fx_gpu, Fy_gpu, Fz_gpu, field_array[j].xq_gpu, field_array[j].yq_gpu, field_array[j].zq_gpu, field_array[j].q_gpu,
                                    surf_array[s].xiDev, surf_array[s].yiDev, surf_array[s].ziDev, surf_array[s].sizeTarDev, int32(Nq), 
                                    REAL(field_array[j].E), int32(NCRIT), int32(param.BlocksPerTwig), block=(param.BSZ,1,1), grid=(GSZ,1)) 
                        aux_x = zeros(Nround)
                        aux_y = zeros(Nround)
                        aux_z = zeros(Nround)
                        Fx_gpu.get(aux_x)
                        Fy_gpu.get(aux_y)
                        Fz_gpu.get(aux_z)

                        aux = aux_x[surf_array[s].unsort]*surf_array[s].normal[:,0] + \
                              aux_y[surf_array[s].unsort]*surf_array[s].normal[:,1] + \
                              aux_z[surf_array[s].unsort]*surf_array[s].normal[:,2]

                    elif param.linearSys=='qualocation':
                        Xsk_aux = gpuarray.to_gpu(array([1/3.,1/3.,1/3.]).astype(REAL))
                        Wsk_aux = gpuarray.to_gpu(array([1.]).astype(REAL))
                        computeRHSKtqual_gpu(Fx_gpu, Fy_gpu, Fz_gpu, field_array[j].xq_gpu, field_array[j].yq_gpu, field_array[j].zq_gpu, field_array[j].q_gpu,
                                    surf_array[s].xiDev, surf_array[s].yiDev, surf_array[s].ziDev, surf_array[s].vertexDev, surf_array[s].AreaDev, 
                                    surf_array[s].sizeTarDev, surf_array[s].kDev, Xsk_aux, Wsk_aux, int32(Nq), 
                                    REAL(param.threshold), REAL(w[0]), int32(NCRIT), int32(param.BlocksPerTwig), block=(param.BSZ,1,1), grid=(GSZ,1)) 

                        aux_x = zeros(Nround)
                        aux_y = zeros(Nround)
                        aux_z = zeros(Nround)
                        Fx_gpu.get(aux_x)
                        Fy_gpu.get(aux_y)
                        Fz_gpu.get(aux_z)

                        aux_x = aux_x[surf_array[s].unsort]
                        aux_y = aux_y[surf_array[s].unsort]
                        aux_z = aux_z[surf_array[s].unsort]

                        aux_x = reshape(aux_x, (len(surf_array[s].triangle),param.K))
                        aux_y = reshape(aux_y, (len(surf_array[s].triangle),param.K))
                        aux_z = reshape(aux_z, (len(surf_array[s].triangle),param.K))

                        aux = surf_array[s].Area * (sum(aux_x*w,axis=1)*surf_array[s].normal[:,0] + \
                                                    sum(aux_y*w,axis=1)*surf_array[s].normal[:,1] + \
                                                    sum(aux_z*w,axis=1)*surf_array[s].normal[:,2]) 



#               For PARENT surface, q contributes to RHS in 
#               INTERIOR equation (hence Precond[0,:] and [2,:])
    
#               No preconditioner
#                F[s_start:s_start+s_size] += aux
#               With preconditioner
                if surf_array[s].surf_type=='asc_surface':
                    F[s_start:s_start+s_size] += aux*surf_array[s].Precond[0,:]
                else:
                    F[s_start:s_start+s_size] += aux[surf_array[s].unsort]*surf_array[s].Precond[0,:]
                    F[s_start+s_size:s_start+2*s_size] += aux[surf_array[s].unsort]*surf_array[s].Precond[2,:]

#   Dirichlet/Neumann contribution to RHS
    for j in range(len(field_array)):

        dirichlet = []
        neumann   = []
        LorY = field_array[j].LorY

#       Find Dirichlet and Neumann surfaces in region
#       Dirichlet/Neumann surfaces can only be child of region,
#       no point on looking at parent surface
        for s in field_array[j].child:
            if surf_array[s].surf_type=='dirichlet_surface':
                dirichlet.append(s)
            elif surf_array[s].surf_type=='neumann_surface':
                neumann.append(s)

        if len(neumann)>0 or len(dirichlet)>0:
            
#           First look at influence on SIBLING surfaces
            for s in field_array[j].child:

                param.kappa = field_array[j].kappa

#               Effect of dirichlet surfaces
                for sd in dirichlet:
                    K_diag = -2*pi*(sd==s)
                    V_diag = 0
                    IorE   = 2 
                    K_lyr, V_lyr = project(surf_array[sd].phi0, zeros(len(surf_array[sd].xi)), LorY, surf_array[sd], 
                            surf_array[s], K_diag, V_diag, IorE, sd, param, ind0, timing, kernel)

#                   Find location of surface s in RHS array
                    s_start = 0
                    for ss in range(s):
                        if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                            s_start += len(surf_array[ss].triangle)
                        else:
                            s_start += 2*len(surf_array[ss].triangle)

                    s_size = len(surf_array[s].triangle)

#                   if s is a charged surface, the surface has only one equation, 
#                   else, s has 2 equations and K_lyr affects the external
#                   equation, which is placed after the internal one, hence
#                   Precond[1,:] and Precond[3,:].
                    if surf_array[s].surf_type=='dirichlet_surface' or surf_array[s].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                        F[s_start:s_start+s_size] += K_lyr * surf_array[s].Precond[0,:]
                    else:
                        F[s_start:s_start+s_size] += K_lyr * surf_array[s].Precond[1,:]
                        F[s_start+s_size:s_start+2*s_size] += K_lyr * surf_array[s].Precond[3,:]

#               Effect of neumann surfaces
                for sn in neumann:
                    K_diag = 0
                    V_diag = 0
                    IorE = 2
                    K_lyr, V_lyr = project(zeros(len(surf_array[sn].phi0)), surf_array[sn].phi0, LorY, surf_array[sn], 
                            surf_array[s], K_diag, V_diag, IorE, sn, param, ind0, timing, kernel)

#                   Find location of surface s in RHS array
                    s_start = 0
                    for ss in range(s):
                        if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                            s_start += len(surf_array[ss].triangle)
                        else:
                            s_start += 2*len(surf_array[ss].triangle)

                    s_size = len(surf_array[s].triangle)

#                   if s is a charged surface, the surface has only one equation, 
#                   else, s has 2 equations and V_lyr affects the external
#                   equation, which is placed after the internal one, hence
#                   Precond[1,:] and Precond[3,:].
                    if surf_array[s].surf_type=='dirichlet_surface' or surf_array[s].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                        F[s_start:s_start+s_size] += -V_lyr * surf_array[s].Precond[0,:]
                    else:
                        F[s_start:s_start+s_size] += -V_lyr * surf_array[s].Precond[1,:]
                        F[s_start+s_size:s_start+2*s_size] += -V_lyr * surf_array[s].Precond[3,:]

#           Now look at influence on PARENT surface
#           The dirichlet/neumann surface will never be the parent, 
#           since we are not solving for anything inside them.
#           Then, in this case we will not look at self interaction.
            if len(field_array[j].parent)==1:

                s = field_array[j].parent[0]


#               Effect of dirichlet surfaces
                for sd in dirichlet:
                    K_diag = 0  
                    V_diag = 0
                    IorE   = 1
                    K_lyr, V_lyr = project(surf_array[sd].phi0, zeros(len(surf_array[sd].xi)), LorY, surf_array[sd], 
                            surf_array[s], K_diag, V_diag, IorE, sd, param, ind0, timing, kernel)

#                   Find location of surface s in RHS array
                    s_start = 0
                    for ss in range(s):
                        if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                            s_start += len(surf_array[ss].triangle)
                        else:
                            s_start += 2*len(surf_array[ss].triangle)

                    s_size = len(surf_array[s].triangle)

#                   Surface s has 2 equations and K_lyr affects the internal
#                   equation, hence Precond[0,:] and Precond[2,:].
                    F[s_start:s_start+s_size] += K_lyr * surf_array[s].Precond[0,:]
                    F[s_start+s_size:s_start+2*s_size] += K_lyr * surf_array[s].Precond[2,:]

#               Effect of neumann surfaces
                for sn in neumann:
                    K_diag = 0
                    V_diag = 0
                    IorE   = 1
                    K_lyr, V_lyr = project(zeros(len(surf_array[sn].phi0)), surf_array[sn].phi0, LorY, surf_array[sn], 
                            surf_array[s], K_diag, V_diag, IorE, sn, param, ind0, timing, kernel)

#                   Find location of surface s in RHS array
                    s_start = 0
                    for ss in range(s):
                        if surf_array[ss].surf_type=='dirichlet_surface' or surf_array[ss].surf_type=='neumann_surface' or surf_array[s].surf_type=='asc_surface':
                            s_start += len(surf_array[ss].triangle)
                        else:
                            s_start += 2*len(surf_array[ss].triangle)

                    s_size = len(surf_array[s].triangle)

#                   Surface s has 2 equations and K_lyr affects the internal
#                   equation, hence Precond[0,:] and Precond[2,:].
                    F[s_start:s_start+s_size] += -V_lyr * surf_array[s].Precond[0,:]
                    F[s_start+s_size:s_start+2*s_size] += -V_lyr * surf_array[s].Precond[2,:]


    return F

def calculateEsolv(surf_array, field_array, param, kernel):

    REAL = param.REAL

#   To compute phir with qualocation, I'll use the targets as Gauss nodes, need to flip back
    if param.linearSys == 'qualocation':
#       If CPU only need to flip the unsorted arrays on the CPU
        if param.GPU==0: 
            for surf in surf_array:
                x_aux = copy(surf.xi)
                surf.xi = copy(surf.xj)
                surf.xj = copy(x_aux)

                x_aux = copy(surf.yi)
                surf.yi = copy(surf.yj)
                surf.yj = copy(x_aux)

                x_aux = copy(surf.zi)
                surf.zi = copy(surf.zj)
                surf.zj = copy(x_aux)
            
                for Cells in surf.tree:
                    x_aux = copy(Cells.source)
                    Cells.source = copy(Cells.target)
                    Cells.target = copy(x_aux)

                    x_aux = Cells.ntarget
                    Cells.ntarget = Cells.nsource
                    Cells.nsource = x_aux

#       If GPU, need to flip and resort, as targets are sorted with voids
        elif param.GPU==1:
            for surf in surf_array:
                sort_aux = zeros(len(surf.xi), dtype=int)
                counter = 0
                for C in surf.twig:
                    sort_aux[counter:counter+surf.tree[C].ntarget] = surf.tree[C].target
                    counter += surf.tree[C].ntarget

                surf.xjDev = gpuarray.to_gpu(surf.xi[sort_aux].astype(REAL))
                surf.yjDev = gpuarray.to_gpu(surf.yi[sort_aux].astype(REAL))
                surf.zjDev = gpuarray.to_gpu(surf.zi[sort_aux].astype(REAL))
                surf.AreaDev = gpuarray.to_gpu(surf.Area[sort_aux/param.K].astype(REAL))
                surf.kDev = gpuarray.to_gpu((sort_aux%param.K).astype(int32))
                surf.vertexDev = gpuarray.to_gpu(surf.vertex[surf.triangle[sort_aux/param.K]].astype(REAL))
                surf.sortSource = sort_aux

    par_reac = parameters()
    par_reac = param
    par_reac.threshold = 0.05
    par_reac.P = 7
    par_reac.theta = 0.0
    par_reac.Nm= (par_reac.P+1)*(par_reac.P+2)*(par_reac.P+3)/6

    ind_reac = index_constant()
    computeIndices(par_reac.P, ind_reac)
    precomputeTerms(par_reac.P, ind_reac)

    par_reac.Nk = 13         # Number of Gauss points per side for semi-analytical integrals

    cal2J = 4.184
    C0 = param.qe**2*param.Na*1e-3*1e10/(cal2J*param.E_0)
    E_solv = []

    ff = -1
    for f in param.E_field:
        parent_type = surf_array[field_array[f].parent[0]].surf_type
        if parent_type != 'dirichlet_surface' and parent_type != 'neumann_surface':

            E_solv_aux = 0
            ff += 1
            print 'Calculating solvation energy for region %i, stored in E_solv[%i]'%(f,ff)
            
            AI_int = 0
            Naux = 0
            phi_reac = zeros(len(field_array[f].q))

#           First look at CHILD surfaces
#           Need to account for normals pointing outwards
#           and E_hat coefficient (as region is outside and 
#           dphi_dn is defined inside)
            for i in field_array[f].child:
                s = surf_array[i]
                s.xk,s.wk = GQ_1D(par_reac.Nk)
                s.xk = REAL(s.xk)
                s.wk = REAL(s.wk)
                for C in range(len(s.tree)):
                    s.tree[C].M  = zeros(par_reac.Nm)
                    s.tree[C].Md = zeros(par_reac.Nm)

                Naux += len(s.triangle)

#               Coefficient to account for dphi_dn defined in
#               interior but calculation done in exterior
                C1 = s.E_hat

                if param.GPU==0:
                    phi_aux, AI = get_phir(s.phi, C1*s.dphi, s, field_array[f].xq, s.tree, par_reac, ind_reac)
                elif param.GPU==1:
                    phi_aux, AI = get_phir_gpu(s.phi, C1*s.dphi, s, field_array[f], par_reac, kernel)
                
                AI_int += AI
                phi_reac -= phi_aux # Minus sign to account for normal pointing out

#           Now look at PARENT surface
            if len(field_array[f].parent)>0:
                i = field_array[f].parent[0]
                s = surf_array[i]
                s.xk,s.wk = GQ_1D(par_reac.Nk)
                s.xk = REAL(s.xk)
                s.wk = REAL(s.wk)
                for C in range(len(s.tree)):
                    s.tree[C].M  = zeros(par_reac.Nm)
                    s.tree[C].Md = zeros(par_reac.Nm)

                Naux += len(s.triangle)

                if param.GPU==0:
                    phi_aux, AI = get_phir(s.phi, s.dphi, s, field_array[f].xq, s.tree, par_reac, ind_reac)
                elif param.GPU==1:
                    phi_aux, AI = get_phir_gpu(s.phi, s.dphi, s, field_array[f], par_reac, kernel)
                
                AI_int += AI
                phi_reac += phi_aux 

            
            E_solv_aux += 0.5*C0*sum(field_array[f].q*phi_reac)
            E_solv.append(E_solv_aux)

            print '%i of %i analytical integrals for phi_reac calculation in region %i'%(AI_int/len(field_array[f].xq),Naux, f)

    return E_solv      


def coulombEnergy(f, param):

    point_energy = zeros(len(f.q), param.REAL)
    coulomb_direct(f.xq[:,0], f.xq[:,1], f.xq[:,2], f.q, point_energy)

    cal2J = 4.184
    C0 = param.qe**2*param.Na*1e-3*1e10/(cal2J*param.E_0)

    Ecoul = sum(point_energy) * 0.5*C0/(4*pi*f.E)
    return Ecoul


def calculateEsurf(surf_array, field_array, param, kernel):

    REAL = param.REAL

    par_reac = parameters()
    par_reac = param
    par_reac.threshold = 0.05
    par_reac.P = 7
    par_reac.theta = 0.0
    par_reac.Nm= (par_reac.P+1)*(par_reac.P+2)*(par_reac.P+3)/6

    ind_reac = index_constant()
    computeIndices(par_reac.P, ind_reac)
    precomputeTerms(par_reac.P, ind_reac)

    par_reac.Nk = 13         # Number of Gauss points per side for semi-analytical integrals

    cal2J = 4.184
    C0 = param.qe**2*param.Na*1e-3*1e10/(cal2J*param.E_0)
    E_surf = []

    ff = -1
    for f in param.E_field:
        parent_surf = surf_array[field_array[f].parent[0]]
         
        if parent_surf.surf_type == 'dirichlet_surface':
            ff += 1
            print 'Calculating surface energy around region %i, stored in E_surf[%i]'%(f,ff)
            Esurf_aux = -sum(-parent_surf.Eout*parent_surf.dphi*parent_surf.phi*parent_surf.Area) 
            E_surf.append(0.5*C0*Esurf_aux)
        
        elif parent_surf.surf_type == 'neumann_surface':
            ff += 1
            print 'Calculating surface energy around region %i, stored in E_surf[%i]'%(f,ff)
            Esurf_aux = sum(-parent_surf.Eout*parent_surf.dphi*parent_surf.phi*parent_surf.Area) 
            E_surf.append(0.5*C0*Esurf_aux)

    return E_surf
