# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

'''
Splitters and user/affinity matrix generation utilities for the RBM algo

'''

import pandas as pd

import numpy as np
import random
import math

import itertools

from scipy.sparse import coo_matrix
import logging


#import default parameters
from reco_utils.common.constants import (
    DEFAULT_USER_COL,
    DEFAULT_ITEM_COL,
    DEFAULT_RATING_COL,
    DEFAULT_TIMESTAMP_COL,
    PREDICTION_COL,
)

from reco_utils.dataset.split_utils import (
    process_split_ratio,
    min_rating_filter_pandas,
    split_pandas_data_with_ratios,
)

#for logging
log = logging.getLogger(__name__)

#========================================================
#Generate the User/Item affinity matrix from a pandas DF
#========================================================


class splitter:

    #initialize class parameters
    def __init__(
        self,
        DF,
        col_user = DEFAULT_USER_COL,
        col_item = DEFAULT_ITEM_COL,
        col_rating = DEFAULT_RATING_COL,
        col_time = DEFAULT_TIMESTAMP_COL,
        save_model = False,
        save_path = 'saver',
        debug = False,
    ):

        self.df = DF #dataframe

        #pandas DF parameters
        self.col_rating = col_rating
        self.col_item = col_item
        self.col_user = col_user

        #Options to save the model for future use
        self.save_model_ = save_model
        self.save_path_ = save_path

    def gen_index(self):

        '''
        Generate the user/item index

        Args:
            DF: a dataframe containing the data

        Returns:
            map_users, map_items: dictionaries mapping the original user/item index to matrix indices
            map_back_users, map_back_items: dictionaries to map back the matrix elements to the original
            dataframe indices

        Basic mechanics:
            As a first step we retieve the unique elements in the dataset. In this way we can take care
            of either completely missing rows (a user with no ratings) or completely missing columns
            (an item that has not being reviewed by anyone). The original indices in the dataframe are
            then mapped to an ordered, contiguous integer series to generate a compact matrix representation.

            Functions to map back to the original indices are also provided and can be saved in order to use
            a pretrained model.

        '''
        #sort entries by user index
        self.df_ = self.df.sort_values(by=[self.col_user])

        #find unique user and item index
        unique_users = self.df_[self.col_user].unique()
        unique_items = self.df_[self.col_item].unique()

        self.Nusers = len(unique_users)
        self.Nitems = len(unique_items)

        #create a dictionary to map unique users/items to hashed values to generate the matrix
        self.map_users = {x:i for i, x in enumerate(unique_users)}
        self.map_items = {x:i for i, x in enumerate(unique_items)}

        #map back functions used to get back the original dataframe
        self.map_back_users = {i:x for i, x in enumerate(unique_users)}
        self.map_back_items = {i:x for i, x in enumerate(unique_items)}

        #optionally save the inverse dictionary to work with trained models
        if self.save_model_:
            np.save(self.save_path_ + '/user_dict', self.map_users)
            np.save(self.save_path_ + '/item_dict', self.map_items)

            np.save(self.save_path_ + '/user_back_dict', self.map_back_users)
            np.save(self.save_path_ + '/item_back_dict', self.map_back_items)



    def gen_affinity_matrix(self):

        '''
        Generate the user/item affinity matrix

        Args:
            DF: A dataframe containing at least UserID, ItemID, Ratings

        Returns:
            RM: user-affinity matrix of dimensions (Nusers, Nitems) in numpy format. Unrated movies
            are assigned a value of 0.

        Basic mechanics:
            As a firts step, two new columns are added to the input DF, containing the index maps
            generated by the gen_index() method. The Dnew indices, together with the ratings, are
            then used to generate the user/item affinity matrix using scipy's sparse matrix method
            coo_matrix; for reference see

            https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.coo_matrix.html

            The input format is coo_matrix((data, (rows, columns)), shape=(rows, columns))

        '''

        log.info("Generating the user/item affinity matrix...")

        self.df_.loc[:, 'hashedItems'] = self.df_[self.col_item].map(self.map_items)
        self.df_.loc[:, 'hashedUsers'] = self.df_[self.col_user].map(self.map_users)

        #extract informations from the dataframe as an array. Note that we substract 1 from itm_id and usr_id
        #in order to map it to matrix format

        r_ = self.df_[self.col_rating]    #ratings
        itm_id = self.df_['hashedItems']  #itm_id serving as columns
        usr_id = self.df_['hashedUsers']  #usr_id serving as rows

        #check that all 3 vectors have the same dimensions
        assert((usr_id.shape[0]== r_.shape[0]) & (itm_id.shape[0] == r_.shape[0]))

        #generate a sparse matrix representation using scipy's coo_matrix and convert to array format
        self.AM = coo_matrix((r_, (usr_id, itm_id)), shape= (self.Nusers, self.Nitems)).toarray()

        #---------------------print the degree of sparsness of the matrix------------------------------

        zero   = (self.AM == 0).sum() # number of unrated items
        total  = self.AM.shape[0]*self.AM.shape[1] #number of elements in the matrix
        sparsness = zero/total *100 #Percentage of zeros in the matrix

        print('Matrix generated, sparsness: %d' %sparsness,'%', 'size:', (self.AM.shape) )



    def map_back_sparse(self, X):

        '''
        Map back the user/affinity matrix to a pd dataframe

        '''
        m, n = X.shape

        #1) Create a DF from a sparse matrix
        #obtain the non zero items
        items  = [ np.asanyarray(np.where(X[i,:] !=0 )).flatten() for i in range(m)]
        ratings = [X[i, items[i]] for i in range(m)] #obtain the non-zero ratings

        #reates user ids following the DF format
        userids = []
        for i in range(0, m):
            userids.extend([i]*len(items[i]) )

        #Flatten the lists to follow the DF input format
        items = list(itertools.chain.from_iterable(items))
        ratings = list(itertools.chain.from_iterable(ratings))

        #create a df
        out_df = pd.DataFrame.from_dict(
            {
                self.col_user  : userids,
                self.col_item  : items,
                self.col_rating: ratings,
            }
        )

        #2) map back user/item ids to their original value

        out_df[self.col_user] = out_df[self.col_user].map(self.map_back_users)
        out_df[self.col_item] = out_df[self.col_item].map(self.map_back_items)

        return out_df


    #====================================
    #Data splitters
    #====================================

    def stratified_split(self, ratio= 0.75, seed= 1234):

        np.random.seed(seed)

        test_cut = int( (1-ratio)*100 )

        self.gen_index()

        maps = [self.map_back_users, self.map_back_items]

        self.gen_affinity_matrix()

        #Test set array
        Xtr  = self.AM.copy()
        Xtst = self.AM.copy()

        #find the number of rated movies per user
        rated = np.sum(Xtr !=0, axis=1)

        #for each user, cut down a test_size% for the test set
        tst = (rated*test_cut)//100

        for u in range(self.Nusers):
            #For each user obtain the index of rated movies
            idx_tst = []
            idx = np.asarray(np.where(np.logical_not(Xtr[u,0:] == 0) )).flatten().tolist()

            #extract a random subset of size n from the set of rated movies without repetition
            for i in range(tst[u]):
                sub_el = random.choice(idx)
                idx.remove(sub_el)
                idx_tst.append(sub_el)

            Xtr[u, idx_tst] = 0  #change the selected rated movies to unrated in the train set
            Xtst[u, idx] = 0  #set the movies that appear already in the train set as 0

            assert(np.sum(Xtr[u,:] != 0) + np.sum(Xtst[u,:] !=0) == rated[u])

        del idx, sub_el, idx_tst

        return Xtr , Xtst, self.map_back_sparse(Xtr), self.map_back_sparse(Xtst), maps