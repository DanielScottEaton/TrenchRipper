import numpy as np
import skimage as sk
import h5py
import os
import copy
import pickle
import shutil
import cv2
import pandas as pd


import scipy.signal as signal
from .utils import kymo_handle,pandas_hdf5_handler,writedir
from .cluster import hdf5lock
from .DetectPeaks import detect_peaks
from time import sleep
from dask.distributed import worker_client
from pandas import HDFStore

class mother_tracker():
    """ Class for tracking growth properties of a mother cell in a trench

    Attributes:
        headpath: path for base analysis directory
        phasesegmentationpath: path for segmented masks (numbered by region)
        phasedatapath: path for tables of cell morphology information from segmentation
        growthdatapath: path to save mother cell tracks
        metapath: path for metadata
        meta_handle: utility object for accessing metadat
        kymodf: metadata for each kymograph

    """
    def __init__(self, headpath):
        self.headpath = headpath
        self.phasesegmentationpath = headpath + "/phasesegmentation"
        self.phasedatapath = self.phasesegmentationpath + "/cell_data"
        self.growthdatapath = headpath +"/growth_data.hdf5"
        
        self.metapath = headpath + "/metadata.hdf5"
        self.meta_handle = pandas_hdf5_handler(self.metapath)
        self.kymodf = self.meta_handle.read_df("kymograph",read_metadata=True)
    
#     @profile
    def get_growth_props(self, file_idx, lower_threshold=0.35, upper_threshold=0.75):
        """ Calculate doubling time and growth rate for all the trenches in a kymograph file.
        Thresholds out trenches that have unloaded or dead cells.

        Args:
            file_idx (int): kymograph file index
            lower_threshold (float): Trench loading lower threshold 
            upper_threshold (float): Trench loading upper threshold
        Outputs:
            file_dt (numpy.ndarray): Doubling times data array for each trench, containing times, doubling times, trench id, fov
            file_dt_smoothed (numpy.ndarray): Doubling times data array with smoothed major axis length signal
            file_growth_rate (numpy.ndarray): Istantaneous growth rate of area and mahjor axis length
        """

        # Load data telling us where in the file each trench is
        file_df = pd.read_hdf(os.path.join(self.phasedatapath, "data_%d.h5" % file_idx))
        file_df = file_df.reset_index()
        file_df = file_df.set_index(["file_trench_index", "trench_cell_index"])
        file_df = file_df.sort_index(level=0)
        trenches = file_df.index.unique("file_trench_index")
        file_dt = []
        file_dt_smoothed = []
        file_growth_rate = []
        for trench in trenches:
            try:
                # identify mother cell as the one with the lowest y coordinate in a trench
                mother_cell_index = file_df.loc[trench].index.unique("trench_cell_index")[0]
                mother_df = file_df.loc[trench, mother_cell_index]
                fov = mother_df["fov"].unique()[0]
                trenchid = mother_df["trenchid"].unique()[0]
            except TypeError:
                continue
            # Get growth properties for a single cell
            dt_data, dt_smoothed_data, growth_rate_data = self.get_mother_cell_growth_props(mother_df, lower_threshold=lower_threshold, upper_threshold=upper_threshold)
            if dt_data is not None:
                # Concatenate with trench and FOV data
                dt_data = np.concatenate([dt_data, np.array([trench]*dt_data.shape[0])[:, None], np.array([trenchid]*dt_data.shape[0])[:, None], np.array([fov]*dt_data.shape[0])[:, None]], axis=1)
                dt_smoothed_data = np.concatenate([dt_smoothed_data, np.array([trench]*dt_smoothed_data.shape[0])[:, None], np.array([trenchid]*dt_smoothed_data.shape[0])[:, None], np.array([fov]*dt_smoothed_data.shape[0])[:, None]], axis=1)
                growth_rate_data = np.concatenate([growth_rate_data, np.array([trench]*growth_rate_data.shape[0])[:, None], np.array([trenchid]*growth_rate_data.shape[0])[:, None], np.array([fov]*growth_rate_data.shape[0])[:, None]], axis=1)
                file_dt.append(dt_data)
                file_dt_smoothed.append(dt_smoothed_data)
                file_growth_rate.append(growth_rate_data)
        
        if len(file_dt) == 0:
            return None, None, None
        # Concatenate into one np array
        file_dt = np.concatenate(file_dt, axis=0)
        file_dt_smoothed = np.concatenate(file_dt_smoothed, axis=0)
        file_growth_rate = np.concatenate(file_growth_rate, axis=0)
        
        # Return as np array instead of pandas dataframe because much faster to do array options, only
        # make pandas dataframe for ALL cells at the end
        file_dt = np.append(file_dt, np.array([file_idx]*file_dt.shape[0])[:, None], axis=1)
        file_dt_smoothed = np.append(file_dt_smoothed, np.array([file_idx]*file_dt_smoothed.shape[0])[:, None], axis=1)
        file_growth_rate = np.append(file_growth_rate, np.array([file_idx]*file_growth_rate.shape[0])[:, None], axis=1)
        return file_dt, file_dt_smoothed, file_growth_rate
    
#     @profile
    def save_all_growth_props(self, file_list=None, lower_threshold=0.35, upper_threshold=0.75):
        """ Save growth properties (doubling time, instantaneous growth rate data for a single cell)

        Args:
            file_list (list): List of kymograph files to subset
            lower_threshold (float): Trench loading lower threshold 
            upper_threshold (float): Trench loading upper threshold
        Outputs:
            None
        """
        # Subset files
        if file_list is None:
            file_list = self.kymodf["File Index"].unique().tolist()
        all_dt = []
        all_dt_smoothed = []
        all_growth_rate = []
        # For each file extract growth properties
        for file_idx in file_list:
            file_dt, file_dt_smoothed, file_growth_rate = self.get_growth_props(file_idx, lower_threshold=lower_threshold, upper_threshold=upper_threshold)
            if file_dt is not None:
                all_dt.append(file_dt)
                all_dt_smoothed.append(file_dt_smoothed)
                all_growth_rate.append(file_growth_rate)
        # Concatenate
        all_dt = np.concatenate(all_dt)
        all_dt_smoothed = np.concatenate(all_dt_smoothed)
        all_growth_rate = np.concatenate(all_growth_rate)
        # Make dataframes for each metric
        all_dt = pd.DataFrame(all_dt, columns=["time", "doubling_time_s", "file_trench_idx", "trenchid", "fov", "file_idx"])
        all_dt_smoothed = pd.DataFrame(all_dt_smoothed, columns=["time", "doubling_time_s", "file_trench_idx", "trenchid", "fov", "file_idx"])
        all_growth_rate = pd.DataFrame(all_growth_rate, columns=["time", "igr_length", "igr_length_smoothed", "igr_area", "igr_area_smoothed", "igr_length_normed", "igr_length_smoothed_normed", "igr_area_normed", "igr_area_smoothed_normed", "file_trench_idx", "trenchid", "fov", "file_idx"])
        # Index by field of view and trench
        all_dt = all_dt.set_index(["fov", "trenchid"])
        all_dt_smoothed = all_dt_smoothed.set_index(["fov", "trenchid"])
        all_growth_rate = all_growth_rate.set_index(["fov", "trenchid"])
        # Save to HDF5
        with HDFStore(os.path.join(self.growthdatapath)) as store:            
            store.put("doubling_times", all_dt, data_columns=True)
            store.put("doubling_times_smoothed", all_dt_smoothed, data_columns=True)
            store.put("growth_rates", all_growth_rate, data_columns=True)
        
    def get_doubling_times(self, times, peak_series, relative_threshold=1):
        """ Calculate doubling times from a signal of cell morphology

        Args:
            times (numpy.ndarray, float): 1 x t array of times corresponding to each measurement
            peak_series (numpy.ndarray, float): 1 x t array of signal to extract peaks from
            relative_threshold (float): relative threshold to check peaks against
        Returns:
            array (numpy.ndarray, float): 2 x t array of peak times and the duration from the last
            doubling time
        """
        peaks = detect_peaks(peak_series, mpd=5, relative_threshold=relative_threshold)
        time_of_doubling = times[peaks]
        doubling_time_s = time_of_doubling[1:]-time_of_doubling[0:len(time_of_doubling)-1]
        return np.array([time_of_doubling[1:], doubling_time_s]).T
        
    def get_mother_cell_growth_props(self, mother_data_frame, lower_threshold=0.35, upper_threshold=0.75):
        """ Get growth properties for a mother cell

        Args:
            mother_data_frame (pandas.Dataframe): Segmentation data of a mother cell
            lower_threshold (float): Trench loading lower threshold 
            upper_threshold (float): Trench loading upper threshold
        Returns:
            doubling_time (numpy.ndarray): Doubling times data array for the trench, containing times, doubling times, and interval to previous doubling time
            doubling_time_smoothed (numpy.ndarray): Smoothed doubling times data array for the trench, containing times, doubling times, and interval to previous doubling time
            growth_rate_data (numpy.ndarray): Growth ratedata array for the trench, containing growth rates for length and area (smoothed and raw)
        """
        # Get loading fractions
        loading_fractions = np.array(mother_data_frame["trench_loadings"])
        # Check whether trench unloads
        cutoff_index = len(loading_fractions)
        times_within_thresholds = (loading_fractions > lower_threshold)*(loading_fractions < upper_threshold)
        times_outside_thresholds = ~times_within_thresholds
        # Throw out trenches once you see a run of more than 5 timepoints with loading outside the loading thresholds
        if cutoff_index < 5:
            return None, None, None
        for i in range(len(loading_fractions)-4):
            if np.all(times_outside_thresholds[i:i+5]):
                cutoff_index = i
                break
        if cutoff_index < 5:
            return None, None, None
        # extract data from dataframes
        times = np.array(mother_data_frame["time_s"])[:cutoff_index]
        major_axis_length = np.array(mother_data_frame["major_axis_length"])[:cutoff_index]
        area = np.array(mother_data_frame["area"])[:cutoff_index]
        # Interpolate individual points where trench loading is messed up,
        # indicative of drift or bad segmentation
        repaired_data = self.repair_trench_loadings(np.array([major_axis_length, area]).T, times_outside_thresholds[:cutoff_index])
        major_axis_length = repaired_data[:,0].flatten()
        area = repaired_data[:,1].flatten()
        
        # Smooth with Wiener filter (minimum mean squared error)
        mal_smoothed = signal.wiener(major_axis_length)
        area_smoothed = signal.wiener(area)
        
        doubling_time = self.get_doubling_times(times, major_axis_length, relative_threshold=1.5)
        doubling_time_smoothed = self.get_doubling_times(times, mal_smoothed, relative_threshold=1.5)
        
        # Calculate growth rate as a gradient
        instantaneous_growth_rate_length = np.gradient(major_axis_length, times)[1:]
        instantaneous_growth_rate_area = np.gradient(area, times)[1:]
        instantaneous_growth_rate_length_smoothed = np.gradient(mal_smoothed, times)[1:]
        instantaneous_growth_rate_area_smoothed = np.gradient(area_smoothed, times)[1:]
        
        growth_rate_data = np.array([times[1:], instantaneous_growth_rate_length, instantaneous_growth_rate_length_smoothed, instantaneous_growth_rate_area, instantaneous_growth_rate_area_smoothed]).T
        
        growth_rate_data = np.concatenate([growth_rate_data, growth_rate_data[:,1:3]/major_axis_length[1:, None], growth_rate_data[:,3:5]/area[1:, None]], axis=1)
                                               
        return doubling_time, doubling_time_smoothed, growth_rate_data
    
    def repair_trench_loadings(self, data_raw, outside_thresholds):
        """ Fix short runs that have bad segmentations

        Args:
            data_raw (numpy.ndarray, float): Raw signal to be interpolated (1 x t)
            outside_thresholds (numpy.ndarray, bool): Whether each timepoint is outside the loading thresholds
        Returns:
            data (numpy.ndarray, float): Interpolated data
        """
        data = np.copy(data_raw)
        for i in range(data.shape[0]):
            if outside_thresholds[i]:
                # If this is the first index, fill from the right
                if i==0:
                    right_idx = 1
                    while outside_thresholds[right_idx]:
                        right_idx += 1
                    for j in range(i, right_idx):
                        data[j,:] = data[right_idx,:]
                # If this is the last index, fill from the left
                elif i == data.shape[0] - 1:
                    left_idx = i - 1
                    while outside_thresholds[left_idx]:
                        left_idx -= 1
                    for j in range(left_idx+1, i+1):
                        data[j,:] = data[left_idx,:]
                # Otherwise interpolate
                else:
                    left_idx = i - 1
                    right_idx = i + 1
                    while outside_thresholds[right_idx] and right_idx < data.shape[0]-1:
                        right_idx += 1
                    while outside_thresholds[left_idx] and left_idx > 0:
                        left_idx -= 1
                    if outside_thresholds[right_idx]:
                        for j in range(left_idx+1, right_idx+1):
                            data[j,:] = data[left_idx,:]
                    elif outside_thresholds[left_idx]:
                        for j in range(left_idx, right_idx):
                            data[j,:] = data[right_idx,:]
                    else:
                        for j in range(left_idx+1, right_idx):
                            data[j,:] = data[left_idx,:] + (data[right_idx,:]-data[left_idx,:]) * (j-left_idx)/(right_idx-left_idx)
        return data