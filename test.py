#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import numpy as np
import scipy.io as sio
import json
import pandas as pd

if __name__ == '__main__':

    with open('/media/sf_Ubuntu_files/git/Cogan_BIDS/BIDS_converter/config.json', 'r') as fst:
        config = json.load(fst)
    mat1 = sio.loadmat('/media/sf_Ubuntu_files/Workspace/Phoneme_Sequencing/D48/D48_Trials.mat')
    mat2 = sio.loadmat('/media/sf_Ubuntu_files/Workspace/Phoneme_Sequencing/D48/D48_trialInfo.mat')

    newmat = np.ndarray(shape=(208,0), dtype=float, order='F')
    newmat_names = []
    #print(typemat1)
    for i in list(mat1):
        if "__" not in i and "readme" not in i:
            if mat1[i].dtype.names is not None:
                print(type(mat1[i].dtype))
                for j in mat1[i].dtype.names:
                    print(np.transpose(mat1[i][j]).shape, newmat.shape)
                    newmat = np.append(newmat, np.transpose(mat1[i][j]),axis=1)
                    newmat_names.append((j,'0'))
    print(type(newmat_names),newmat.dtype.fields)
    mat1 = np.array(newmat,dtype=newmat_names)
    print(mat1.dtype, "here")
    mat1.dtype = newmat_names
    print(mat1)
        

