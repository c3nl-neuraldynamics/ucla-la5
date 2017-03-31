#!/share/apps/anaconda/bin/python
# -*- coding: ascii -*-
from __future__ import division
import matplotlib
matplotlib.use('Agg')  # allow generation of images without user interface
import matplotlib.pyplot as plt
import numpy as np
import pickle
import os
from math import log
from nitime.timeseries import TimeSeries
from nitime.analysis import FilterAnalyzer
from scipy.signal import hilbert
from bct import (degrees_und, distance_bin, transitivity_bu, clustering_coef_bu,
                 randmio_und_connected, charpath, clustering)
from sklearn.cluster import KMeans
import nibabel as nib
import progressbar
import pdb


def extract_roi(subjects,
                network_type,
                input_basepath,
                segmented_image_filename,
                segmented_regions_filename,
                output_basepath,
                network_mask_filename=None):
    """
    Iterate over all subjects and all regions (specified by the segmented_image).
     For each region find the correspoding BOLD signal. To reduce the
     dimensionality the signal belonging to the same anatomical regions are
     averaged for each time point. The BOLD signal for each region is then saved
     in a txt file for each subject

     Inputs:
         - subjects_id   : List of subjects id
         - fwhm          : used fwhm
         - input_basepath: Path to datasink
         - preprocessed_image: name of the preprocessed file that will be
                               segmented
         - segmented_image_filename : Path to the image that will be used to segment
         - segmented_regions: List of regions that will be used for the
           segmentation
         - output_basepath   : Path where the BOLD signal will be saved
         - newtork       : Define if segmented regions should be further
                           combined into networks
         - network_path  : Path to the image where the different networks are
                           specified
         - network_comp  : Allow for comparison between networks and inside
                           networks
     """

    # Only full_network does not require a network mask.
    if network_type != 'full_network' and network_mask_filename is None:
        raise ValueError('The %s network type requires a network mask.' %
                         (network_type))

    # Load the segmented regions list.
    segmented_regions = np.genfromtxt(segmented_regions_filename,
        dtype = [('numbers', '<i8'), ('regions', 'S31'), ('labels', 'i4')],
        delimiter=','
    )

    # Load the segmented image.
    segmented_image = nib.load(segmented_image_filename)
    segmented_image_data = segmented_image.get_data()

    # Extract ROI for each subjects.
    preprocessed_image_filename = 'denoised_func_data_nonaggr_filt.nii.gz'
    for subject in subjects:
        print 'Analysing subject: %s.' % subject

        # Generate the output folder.
        subject_path = os.path.join(output_basepath, subject)
        if not os.path.exists(subject_path):
            os.makedirs(subject_path)

        # Load the subject input image.
        image_filename = os.path.join(input_basepath, 'final_image', subject,
                                      preprocessed_image_filename)
        image = nib.load(image_filename)
        image_data = image.get_data()
        ntpoints = image_data.shape[3]

        if network_type == 'full_network':
            # Calculate the average BOLD signal over all regions.
            avg = np.zeros((segmented_regions['labels'].shape[0], ntpoints))
            for region in range(len(segmented_regions)):
                label = segmented_regions['labels'][region]
                boolean_mask = np.where(segmented_image_data == label)
                for t in range(ntpoints):
                    data = image_data[:, :, :, t]
                    data = data[boolean_mask[0], boolean_mask[1], boolean_mask[2]]
                    avg[region, t] = data.mean()

            # Dump the results.
            np.savetxt(os.path.join(subject_path, 'full_network.txt'),
                       avg, delimiter=' ', fmt='%5e')
        else:
            # Load the network mask.
            ntw_image = nib.load(network_mask_filename)
            ntw_data = ntw_image.get_data()
            boolean_ntw = ntw_data > 1.64

            # Find the most likely regions inside the network.
            networks = {key: [] for key in range(ntw_data.shape[3])}
            ntw_filter = np.zeros(ntw_data.shape)
            for region in range(len(segmented_regions)):
                label = segmented_regions['labels'][region]
                boolean_mask = segmented_image_data == label
                networks, ntw_filter = most_likely_roi_network(networks,
                                                               ntw_data,
                                                               ntw_filter,
                                                               boolean_ntw,
                                                               boolean_mask,
                                                               region)

            if network_type == 'within_network':
                # Calculate the BOLD signal for the selected regions in the
                # network. The labels from the original segmentation will be used
                # to identify the regions of interest
                for network in networks:
                    avg = np.zeros((len(networks[network]), ntpoints))
                    for region in range(len(networks[network])):
                        label = segmented_regions['labels'][networks[network][region]]
                        boolean_mask = np.where(segmented_image_data == label)
                        for t in range(ntpoints):
                            data = image_data[:, :, :, t]
                            data = data[boolean_mask[0], boolean_mask[1], boolean_mask[2]]
                            avg[region, t] = data.mean()
                    np.savetxt(os.path.join(subject_path,
                                            'within_network_%d.txt' % network),
                               avg, delimiter=' ', fmt='%5e')
            elif network_type == 'between_network':
                # Calculate the BOLD signal across the selected networks. This procedure is similar to the full
                # network approach, however, the BOLD activity of all regions enclosed in one network is taken
                # into account.
                avg = np.zeros((ntw_data.shape[3], ntpoints))
                for network in range(ntw_data.shape[3]):
                    boolean_mask = np.where(ntw_filter[:, :, :, network] > 0)
                    for t in range(ntpoints):
                        data = image_data[:, :, :, t]
                        data = data[boolean_mask[0], boolean_mask[1], boolean_mask[2]]
                        avg[network, t] = data.mean()
                np.savetxt(os.path.join(subject_path,
                                        'between_network.txt'),
                           avg, delimiter=' ', fmt='%5e')
            else:
                raise ValueError('Unrecognised network type: %s.' % (network_type))


def most_likely_roi_network(netw, ntw_data, net_filter, boolean_ntw, boolean_mask, region):
    """ iterate over each network and find the one with the highest probability of
    including a specific region. Once the best network is found, compute the mean bold from
    that region. The mean bold will be used to compare among regions that belong
    to the same network"""

    p_network = 0
    for n_network in range(ntw_data.shape[3]):
        # find voxels that correspond to that region in the current network
        filtered_mask = np.multiply(boolean_ntw[:, :, :, n_network], boolean_mask)
        tmp = np.sum(filtered_mask) / float(np.sum(boolean_ntw[:, :, :, n_network]))
        if tmp > p_network:
            netw[n_network].append(region)
            net_filter[:, :, :, n_network] = np.add(filtered_mask, net_filter[:, :, :, n_network])
            p_network = tmp
    return netw, net_filter


def compute_hilbert_tranform(data, TR=2, upper_bound=0.07, lower_bound=0.04):
    """ Perform Hilbert Transform on given data. This allows extraction of phase
     information of the empirical data"""
    # Initialise TimeSeries object
    T = TimeSeries(data, sampling_interval=TR)
    # Initialise Filter and set band pass filter to be between 0.04 and 0.07 Hz
    # - as described on the Glerean-2012Functional paper
    F = FilterAnalyzer(T, ub=upper_bound, lb=lower_bound)
    # Obtain Filtered data from the TimeSeries object
    filtered_data = F.filtered_fourier[:]
    # Demean data: Each region (row)  is subtracted to its own mean
    for row in range(filtered_data.shape[0]):
        filtered_data[row] -= filtered_data[row].mean()

    # Perform Hilbert transform on filtered and demeaned data
    hiltrans = hilbert(filtered_data)
    # discard first and last 10 time steps to avoid border effects caused by the
    # Hilbert tranform
    hiltrans = hiltrans[:, 10:-10]
    return hiltrans


def slice_window_avg(array, window_size):
    """ Perform convolution on the specified sliding window. By using the
    'valid' mode the last time points will be discarded. """
    window = np.ones(int(window_size)) / float(window_size)
    return np.convolve(array, window, 'valid')
    # TODO: use a less conservative approach for convolution ('full' instead of
    # 'valid').


def apply_sliding_window(hilbert_transform, window_size):
    nregions = hilbert_transform.shape[0]
    ntpoints = hilbert_transform.shape[1]
    slided = np.zeros((nregions, ntpoints - window_size + 1), dtype=complex)
    for roi in range(nregions):
        slided[roi, :] = slice_window_avg(hilbert_transform[roi, :],
                                          window_size)
    return slided


def calculate_phi(hiltrans):
    n_regions = hiltrans.shape[0]
    hilbert_t_points = hiltrans.shape[1]

    # Find indices of regions for pairwise comparison and reshuffle them
    # to obtain a tuple for each pairwise comparison.
    # As the comparision is symmetric computation power can be saved by
    # calculating only the lower diagonal matrix.
    indices = np.tril_indices(n_regions)
    indices = zip(indices[0], indices[1])

    phi = np.zeros((n_regions, n_regions, hilbert_t_points), dtype=complex)
    pair_synchrony = np.zeros((n_regions, n_regions))
    pair_metastability = np.zeros((n_regions, n_regions))
    # find the phase angle of the data
    phase_angle = np.angle(hiltrans)

    for index in indices:
        # obtain phase of the data is already saved inside hiltrans
        # calculate pairwise order parameter
        phi[index[0], index[1], :] += np.exp(phase_angle[index[0]] * 1j)
        phi[index[0], index[1], :] += np.exp(phase_angle[index[1]] * 1j)

        # divide the obtained results by the number of regions, which in
        # this case is 2.
        phi[index[0], index[1], :] /= 2
        # each value represent the synchrony between two regions over all time points
        pair_synchrony[index[0], index[1]] = np.mean(abs(phi[index[0], index[1], :]))
        # each value represent the standard deviation of synchrony over the time points
        pair_metastability[index[0], index[1]] = np.std(abs(phi[index[0], index[1], :]))
    synchrony = abs(phi)
    # mirror array so that lower and upper matrix are identical
    # Mirror each time point of synchrony
    for time_p in range(synchrony.shape[2]):
        synchrony[:, :, time_p] = mirror_array(synchrony[:, :, time_p])
    pair_synchrony = mirror_array(pair_synchrony)
    pair_metastability = mirror_array(pair_metastability)
    global_synchrony = np.mean(np.tril(pair_synchrony), -1)
    global_metastability = np.std(global_synchrony)
    return synchrony, pair_synchrony, pair_metastability, \
           global_synchrony, global_metastability


def mirror_array(array):
    """ Mirror results obtained on the lower diagonal to the Upper diagonal """
    return array + np.transpose(array) - np.diag(array.diagonal())


def calculate_optimal_k(mean_synchrony, indices, k_lower=0.1, k_upper=1.0, k_step=0.01):
    """ Iterate over different threshold (k) to find the optimal value to use a
    threshold. This function finds the optimal threshold that allows the
    trade-off between cost and efficiency to be minimal.

    In order obtain the best threshold for all time points, the mean of the
    synchrony over time is used as the connectivity matrix.

    The here implemented approach was based on Bassett-2009Cognitive

    """
    # obtain the number of regions according to the passed dataset
    n_regions = mean_synchrony.shape[0]

    EC_optima = 0  # cost-efficiency
    k_optima = 0  # threshold
    for k in np.arange(k_lower, k_upper, k_step):
        # Binarise connection matrix according with the threshold
        mean_synchrony_bin = np.zeros((mean_synchrony.shape))
        for index in indices:
            if mean_synchrony[index[0], index[1]] >= k:
                mean_synchrony_bin[index[0], index[1]] = 1
        mean_synchrony_bin = mirror_array(mean_synchrony_bin)

        # calculate the shortest path length between each pair of regions using
        # the geodesic distance
        D = distance_bin(mean_synchrony_bin)
        # calculate cost
        C = estimate_cost(n_regions, mean_synchrony_bin)

        # Calculate the distance for the regional efficiency at the current
        # threshold
        E_reg = np.zeros((n_regions))
        for ii in range(D.shape[0]):
            sum_D = 0
            for jj in range(D.shape[1]):
                # check if the current value is different from inf or 0 and
                # sum it (inf represents the absence of a connection)
                if jj == ii:
                    continue
                elif D[ii, jj] == np.inf:
                    continue
                else:
                    sum_D += 1 / float(D[ii, jj])
            E_reg[ii] = sum_D / float(n_regions - 1)

        # From the regional efficiency calculate the global efficiency for the
        # current threshold
        E = np.mean(E_reg)
        # update the current optimal Efficiency
        if E - C > EC_optima:
            EC_optima = E - C
            k_optima = k
    return k_optima


def estimate_cost(N, G):
    """ Calculate costs using the formula described in Basset-2009Cognitive """
    tmp = 0
    for ii in range(G.shape[0]):
        for jj in range(G.shape[1]):
            if jj == ii:
                continue
            tmp += G[ii, jj]
        cost = tmp / float(N * (N - 1))
    return cost


def estimate_small_wordness(synchrony_bin, rand_ind):
    """ Estimate small-wordness coefficient. Every time this function is called,
    a new random network is generated.

    Returns
    --------
    SM: small world coefficient
    Ds: distance matrix. Whith lenght of the shortest matrix
    """

    G_rand = randmio_und_connected(synchrony_bin, rand_ind)[0]
    # Calculate clustering coefficient for the random and binary
    # synchrony matrix
    CC = clustering_coef_bu(synchrony_bin)
    CC_rand = clustering_coef_bu(G_rand)
    # Estimate characteristic path lenght for random and binary
    # synchrony matrix
    # To calculate the characteristic path lenght the distance between
    # nodes is needed
    Ds = distance_bin(synchrony_bin)
    Ds_rand = distance_bin(G_rand)
    # The first element of the returned array correspond to the
    # characteristic path lenght
    L = charpath(Ds)[0]
    L_rand = charpath(Ds_rand)[0]

    CC_sm = np.divide(CC, CC_rand)
    L_sm = np.divide(L, L_rand)
    SM = np.divide(CC_sm, L_sm)
    return SM, Ds


def shanon_entropy(labels):
    """ Computes Shanon entropy using the labels distribution """
    n_labels = labels.shape[0]

    # check number of labels and if there is only 1 class return 0
    if n_labels <= 1:
        return 0

    # bincount return the counts in an ascendent format.
    counts = np.bincount(labels)
    probs = counts / float(n_labels)
    n_classes = probs.shape[0]

    if n_classes <= 1:
        return 0

    ent = 0

    # Compute Shannon Entropy
    ss = 0
    sq = 0
    for prob in probs:
        ent -= prob * log(prob, 2)
        ss += prob * (log(prob, 2)) ** 2
        sq += (prob * log(prob, 2)) ** 2
    s2 = ((ss - sq) / float(n_labels)) - ((n_classes - 1) / float(2 *
                                                                  (n_labels) ** 2))
    return ent, s2, n_labels, n_classes  # count is an array you might want to


# return n_classes instead


def bold_plot_threshold(data, n_regions, threshold=1.3):
    """ This function thresholds the BOLD activity using the passed threshold  """
    # Calculate states on raw BOLD data
    z_data = np.zeros((data.shape))
    thr_data = np.zeros((data.shape))
    for VOI in range(n_regions):
        voi_mean = np.mean(data[VOI, :])
        voi_std = np.std(data[VOI, :])
        for t in range(data.shape[1]):
            z_data[VOI, t] = abs(float((data[VOI, t] - voi_mean)) / voi_std)
            # Threshold BOLD at 1.3
            if z_data[VOI, t] > threshold:
                thr_data[VOI, t] = 1
    return thr_data


def data_analysis_subject_basepath(basepath,
                                   network_type,
                                   window_type,
                                   subject):
    return os.path.join(basepath, network_type, window_type, subject)


def data_analysis_full_path(basepath,
                            network_type,
                            window_type,
                            subject,
                            data_analysis_type,
                            nclusters,
                            rand_ind):
    return os.path.join(data_analysis_subject_basepath(basepath,
                                                       network_type,
                                                       window_type,
                                                       subject),
                        data_analysis_type, 'nclusters_' + str(nclusters),
                        'rand_ind_' + str(rand_ind))


def data_analysis(subjects,
                  input_basepath,
                  output_basepath,
                  network_type,
                  window_type,
                  data_analysis_type,
                  nclusters,
                  rand_ind,
                  graph_analysis=True, # FIXME
                  window_size=20, # FIXME
                  n_time_points=184,
                  nnetworks=10): # FIXME remove default nnetworks
    ''' Compute the main analysis. This function calculates the synchrony,
    metastability and perform the graph analysis.

    Inputs:
        - subjects_id:    A list of subjects_id
        - rand_ind:       Randomisation index -- necessary for generating random
                          matrix
        - analysis_type:  Define type of analysis to be performed. Possbile
                          inputs: synchrony or BOLD.
        - nclusters:      Number of clusters used for k-means
        - sliding_window: Sliding window used to reduce noise of the time serie
        - graph_analysis: Defines if graph_analysis will be performed or not
        - window_size:    Defined size of the sliding window
        - n_time_points:  number ot time points of the data set
        - n_regions:      Define number of regions used in the data set
        - network_comp:   Define type of coparision that will be carried out.
                          between_network = compare BOLD between network
                          within_network = compare BOLD within network
                          full_network = compare BOLD from all regions used in the
                          segmentation
        - n_network:      number of networks of interested (only needed when looking at the
                          within network comparison)
    '''

    # Compute synchrony, metastability and mean synchrony for each subject, both
    # globally and pairwise.
    for subject in subjects:
        # Calculate Hilbert transform for the network(s).
        # Import ROI data for each VOI.
        # The actual data depends on the network type.
        hilbert_transforms = {}
        if network_type == 'between_network':
            data_path = os.path.join(input_basepath, subject, 'between_network.txt')
            data = np.genfromtxt(data_path)
            hilbert_transforms[0] = compute_hilbert_tranform(data)
        elif network_type == 'within_network':
            for network in range(nnetworks):
                data_path = os.path.join(input_basepath, subject, 'within_network_%d.txt' % network)
                data = np.genfromtxt(data_path)
                hilbert_transforms[network] = compute_hilbert_tranform(data)
        elif network_type == 'full_network':
            data_path = os.path.join(input_basepath, subject, '%s.txt' % subject)
            data = np.genfromtxt(data_path)
            hilbert_transforms[0] = compute_hilbert_tranform(data)
        else:
            raise ValueError('Unrecognised network type: %s' % (network_type))

        # Calculate data synchrony following Hellyer-2015_Cognitive.
        dynamic_measures = {}
        for network in hilbert_transforms:
            # Apply sliding windowing if required.
            hilbert_transform = hilbert_transforms[network]
            if window_type == 'sliding':
                hilbert_transform = apply_sliding_window(hilbert_transform,
                                                         window_size)

            # Calculate synchrony, metastability and mean synchrony.
            synchrony, \
            mean_synchrony, \
            metastability, \
            global_synchrony, \
            global_metastability = calculate_phi(hilbert_transform)

            # Save the results for later dump.
            dynamic_measures[network] = {
                'synchrony': synchrony,
                'metastability': metastability,
                'mean_synchrony': mean_synchrony,
                'global_synchrony': global_synchrony,
                'global_metastability': global_metastability
            }

        # Dump results for all networks, for this subject, into a pickle file.
        subject_path = data_analysis_subject_basepath(output_basepath,
                                                      network_type, window_type,
                                                      subject)
        if not os.path.exists(subject_path):
            os.makedirs(subject_path)
        pickle.dump(dynamic_measures,
                    open(os.path.join(subject_path, 'dynamic_measures.pickle'),
                         'wb'))

    # TODO:
    # 1. Compute sincrony, metastability, etc. and save them to a pickle file.
    #    This needs to be done in all configurations anyway, but their actual
    #    value depends on the network_type.
    # 2. Think about early return.

    # FIXME: Move away
    # Initialise matrices for average comparision between regions and index
    # counter
    hilbert_t_points = n_time_points - window_size
    all_phi = np.zeros((len(subjects), 3))
    phi = np.zeros((hilbert_t_points), dtype=complex)
    idx = 0

    hc_subjects_id = []
    # Iterate over the list of subjects and calculate the pairwise and global
    # metastability and synchrony.
    for subject in subjects:
        # n_regions corresponds to the number of regions in the dataset. In the case of the full-
        # network there are 82, for the within network this number varies according to the current
        # network under analysis.
        # used for the segmentation
        # todo: check if this still works with len or if need to revert back to .shape
        n_regions = hiltrans.shape[0]

        if sliding_window:
            hiltrans_sliding_window = np.zeros((n_regions, (hiltrans.shape[1] -
                                                            window_size + 1)), dtype=complex)
            for roi in range(n_regions):
                hiltrans_sliding_window[roi, :] = slice_window_avg(hiltrans[roi, :], window_size)

        # When analysing the BOLD data we need to threshold the raw data so that
        # we have a binary matrix. The threshold here is set to 1.3. The
        # binarised matrix is then passed to kmeans, which finds possible
        # clusters of similar BOLD activity inside the data. Every time point is
        # in this way assigned to a cluster. After this, the function will be
        # calculated for the next subjct, as this analysis does not need to be
        # calculated for each pair of region.
        if data_analysis_type == 'BOLD':
            thr_data = bold_plot_threshold(data, n_regions, threshold=1.3)
            # Save thresholded image of BOLD
            fig = plt.figure()
            plt.imshow(thr_data, interpolation='nearest')
            fig.savefig(os.path.join(output_basepath, grouping_type, network_type,
                                     'rand_ind_%02d' % rand_ind, '%s' % subject,
                                     'threshold_matrix', '%s_BOLD.png' % (subject)))
            plt.clf()
            plt.close()

            # Perfom k-means on the BOLD signal
            bold_shanon_entropy = {}
            kmeans_bold = KMeans(n_clusters=nclusters)
            kmeans_bold.fit_transform(np.transpose(thr_data))
            kmeans_bold_labels = kmeans_bold.labels_
            pdb.set_trace()
            # Calculate Shannon Entropy
            bold_shanon_entropy['bold_h'], bold_shanon_entropy['s2'], \
            bold_shanon_entropy['n_labels_bold'], \
            bold_shanon_entropy['n_classes_bold'] = shanon_entropy(kmeans_bold_labels)
            pickle.dump(bold_shanon_entropy, open(os.path.join(output_basepath,
                                                               grouping_type, network_type,
                                                               'rand_ind_%02d' % rand_ind,
                                                               '%s' % subject, '%02d_clusters' % nclusters,
                                                               'bold_shannon_%s.pickle' % (subject)), 'wb'))
            continue

        # Calculate data synchrony following Hellyer-2015_Cognitive
        if grouping_type == 'pairwise':
            # Find length of time points after Hilbert Transform and/or sliding window
            if sliding_window:
                hilbert_t_points = hiltrans_sliding_window.shape[1]
                # overwrite hiltrans with the data obtained with the sliding window
                hiltrans = hiltrans_sliding_window
            else:
                hilbert_t_points = hiltrans.shape[1]

            # Find indices of regions for pairwise comparison. As the comparision is
            # symmetric computation power can be saved by calculating only the lower
            # diagonal matrix.
            indices = np.tril_indices(n_regions)
            # reshuffle index and obtain tuple for each pairwise comparision
            indices = zip(indices[0], indices[1])

            # Calculate phi, metastability, synchrony and mean synchrony  for the specified indices
            synchrony, mean_synchrony, metastability = calculate_phi(indices, n_regions, hilbert_t_points, hiltrans)
            # save values for metastability
            pickle.dump(metastability, open(os.path.join(output_basepath,
                                                         grouping_type, network_type,
                                                         'rand_ind_%02d' % rand_ind, '%s'
                                                         % subject, 'metastability', 'mean_metastability.pickle'),
                                            'wb'))

            pickle.dump(synchrony, open(os.path.join(output_basepath,
                                                     grouping_type, network_type, 'rand_ind_%02d' % rand_ind,
                                                     '%s'
                                                     % subject, 'synchrony', 'synchrony.pickle'), 'wb'))

            pickle.dump(mean_synchrony, open(os.path.join(output_basepath,
                                                     grouping_type, network_type, 'rand_ind_%02d' % rand_ind,
                                                     '%s'
                                                          % subject, 'synchrony', 'mean_synchrony.pickle'), 'wb'))

            # from the list of subjects get only healthy subjects (coding starts with 1000). This list
            # will be used to get the optimal thresholding value only from healthy subjects
            if int(subject.strip('sub-')) < 40000:
                hc_subjects_id.append(subject)
            # plot synchrony matrix for each time point
            # TODO: to speed up performace a bit you could implement this method:
            # http://stackoverflow.com/questions/16334588/create-a-figure-that-is-reference-counted/16337909#16337909
            # fig = plt.figure()
            # for t in range(hilbert_t_points):
            #     plt.imshow(synchrony[:, :, t], interpolation='nearest')
            #     print out_dir
            #     fig.savefig(os.path.join(out_dir, 'pairwise_comparison', network_comp,
            #         'rand_ind_%02d' %rand_ind, '%s' %subject_id, 'synchrony',
            #         '%s_%03d.png' %(subject_id, t)))
            #     plt.clf()
            # plt.close()

            # ---------------------------------------------------------------------
            # Graph Theory Measurements
            # ---------------------------------------------------------------------
            # Calculate optimal threshold that optimises the cost-efficiency of
            # the current network..

    if grouping_type == 'pairwise':
        k_hc_optima = []
        # calculate optimal thresold on healthy subjects only
        for hc_subject in hc_subjects_id:
            # load subjects mean_synchrony
            path_mean_synchrony = os.path.join(output_basepath, grouping_type, network_type, 'rand_ind_%02d' % rand_ind,
                         '%s' % hc_subject, 'synchrony', 'mean_synchrony.pickle')
            mean_synchrony = pickle.load(open(path_mean_synchrony, 'rb'))
            temp_k_optima = calculate_optimal_k(mean_synchrony, indices)
            k_hc_optima.append(temp_k_optima)

        # find optimal mean of healthy subjects
        k_optima = np.mean(k_hc_optima)

        print ('Optimal mean threshold: %3f' % k_optima)

        for subject in subjects:
            # Threshold the synchrony matrix at each time point using the just found optimal
            # threshold and save the output
            synchrony_bin = np.zeros((n_regions, n_regions, hilbert_t_points))
            # fig = plt.figure()
            for t in range(hilbert_t_points):
                # load subject's synchrony
                path_synchrony = os.path.join(output_basepath, grouping_type, network_type, 'rand_ind_%02d' % rand_ind,
                                                   '%s' % subject, 'synchrony', 'synchrony.pickle')
                synchrony = pickle.load(open(path_synchrony, 'rb'))

                for index in indices:
                    if synchrony[index[0], index[1], t] >= k_optima:
                        synchrony_bin[index[0], index[1], t] = 1

                synchrony_bin[:, :, t] = mirror_array(synchrony_bin[:, :, t])
                # plt.imshow(synchrony_bin[:,:,t], interpolation='nearest')
                # fig.savefig(os.path.join(out_dir, 'pairwise_comparison', network_comp,
                #     'rand_ind_%02d' %rand_ind, '%s' %subject_id,
                #     'threshold_matrix', '%s_%03d.png' %(subject_id, t)))
                # plt.clf()
            # plt.close()
            # pickle.dump(synchrony_bin, open(os.path.join(out_dir,
            #     'pairwise_comparison',  network_comp, 'rand_ind_%02d' %rand_ind, '%s'
            #     %subject_id, 'metastability', 'synchrony_bin.pickle'),
            #     'wb'))

            if graph_analysis == True:
                graph_measures_pickle = os.path.join(output_basepath, grouping_type, network_type,
                                                     'rand_ind_%02d' % rand_ind, '%s' % subject,
                                                     '%02d_clusters' % nclusters,
                                                     'graph_measures_%s.pickle' % subject)
                # check if pickle with files already exist. It it exists than
                # just load it, otherwise perform calculations.
                if not os.path.isfile(graph_measures_pickle):

                    print('Calculating Graph Theory Measurements')
                    # Degree centrality:
                    # -------------------
                    degree_centrality = np.transpose(degrees_und(synchrony_bin))

                    weight = np.zeros((hilbert_t_points, n_regions))
                    w = np.multiply(synchrony, synchrony_bin)
                    # Initialise flatten array so that you have time by regions (140 x
                    # 6724), this strucutre is necessary in order to perform
                    # K-means
                    # Ds_flat = np.zeros((hilbert_t_points, (synchrony_bin.shape[0])**2))
                    Ds_flat = {}
                    SM = {}
                    Ds = {}
                    # SM = np.zeros((hilbert_t_points, n_regions))
                    # Ds = np.zeros((n_regions, n_regions, hilbert_t_points))
                    if network_type == 'between_network':
                        network = range(n_regions)
                        network_list = {}
                        for t in range(hilbert_t_points):
                            network_list[t] = network

                    # # check where the network has more then one component
                    # for t in range(hilbert_t_points):
                    #     n_components = clustering.number_of_components(synchrony_bin[:,:,t])
                    #     if len(np.where(clustering.get_components(synchrony_bin[:,:,t])[0]>1)[0])>1:
                    #         print t
                    # Iterate over time to obtain different complex network measurements.
                    for t in range(hilbert_t_points):

                        # Weight
                        # -------------------
                        # Use the thresholded matrix to calculate the average weight over all regions
                        for roi in range(n_regions):
                            weight[t, roi] = np.average(w[:, roi, t])

                        # Transitivity:
                        # -------------------
                        transitivity = transitivity_bu(synchrony_bin[:, :, t])

                        # Small-worldness
                        # ------------------
                        # Every time this function is called a new random network is
                        # generated
                        print(t)
                        n_components = clustering.number_of_components(synchrony_bin[:, :, t])
                        if n_components > 1:
                            components = dict([(key, []) for key in range(n_components)])
                            # Get all components and transform numpy array into a python list
                            # list_components = clustering.get_components(synchrony_bin[:,:,t])[0].tolist()
                            list_components = clustering.get_components(synchrony_bin[:, :, t])[0]
                            # Check if all components are composed of more then one region.
                            # If so, divide the current network into the corresponding
                            # components, otherwise eliminate the lonely component
                            # get_components()[0]:  ensure that only the vector of
                            # component assignments for each node is returned
                            # if len(np.where(clustering.get_components(synchrony_bin[:,:,t])[0]>1)[0]) == 1:
                            for component in range(1, n_components + 1):
                                if np.bincount(list_components)[component] == 1:
                                    # transform list into np.array to use np.where
                                    # list_components = np.array(list_components)
                                    index_to_eliminate = np.where(list_components == component)[0][0]
                                    # Eliminate first the specified row and then the
                                    # specified column from the thresholded synchrony matrix
                                    tmp = np.delete(synchrony_bin[:, :, t],
                                                    index_to_eliminate, 0)
                                    tmp2 = np.delete(tmp, index_to_eliminate, 1)
                                    # eliminate the specific network form the network list.
                                    network_list[t] = np.delete(network_list[t],
                                                                index_to_eliminate, 0)
                                    print('Node #%d was eliminated at timepoint %d') % (index_to_eliminate, t)
                                    SM[str(t)], Ds[str(t)] = estimate_small_wordness(tmp2, rand_ind)
                                    # Flatten the synchrony matrix and path_distance so that it can be given as
                                    # argument for the K-means
                                    Ds_flat[str(t)] = np.ndarray.flatten(Ds[str(t)])

                                elif 2 <= np.bincount(list_components)[component] < n_regions - 1:
                                    # check if there is more then one component
                                    print('More then one component found at timepoint %d') % (t)
                                    # As all components start from one, iteration should
                                    # start from 1 and not 0.
                                    # find index of the elements belonging to this
                                    # component
                                    all_indices = range(n_regions)
                                    # find indices for each component
                                    indices = np.where(list_components == component)[0]
                                    # obtain indices for elements that will be
                                    # discarted
                                    indices_2_eliminate = np.delete(all_indices, indices, 0)
                                    tmp = np.delete(synchrony_bin[:, :, t], indices_2_eliminate, 1)
                                    # final matrix where the binary synchrony values
                                    # are saved
                                    components[component] = np.delete(tmp, indices_2_eliminate, 0)
                                    # Estimate small-wordness for each component
                                    element = ''.join((str(t), '_', str(component)))
                                    SM[element], Ds[element] = estimate_small_wordness(components[component], rand_ind)
                                    # Flatten the synchrony matrix and path_distance so that it can be given as
                                    # argument for the K-means
                                    Ds_flat[element] = np.ndarray.flatten(Ds[element])
                                else:
                                    continue
                        else:
                            SM[str(t)], Ds[str(t)] = estimate_small_wordness(synchrony_bin[:, :, t], rand_ind)
                            # Flatten the synchrony matrix and path_distance so that it can be given as
                            # argument for the K-means
                            Ds_flat[str(t)] = np.ndarray.flatten(Ds[str(t)])

                    # Save pickle with graph measurements for each subject
                    graph_measures = {'weight': weight,
                                      'small_wordness': SM,
                                      'degree_centrality': degree_centrality,
                                      'path_distance': Ds_flat
                                      }
                    pickle.dump(graph_measures, open(os.path.join(output_basepath,
                                                                  grouping_type, network_type,
                                                                  'rand_ind_%02d' % rand_ind,
                                                                  '%s' % subject, '%02d_clusters' % nclusters,
                                                                  'graph_measures_%s.pickle' % (subject)), 'wb'))
                else:
                    graph_measures = pickle.load(open(graph_measures_pickle, 'rb'))
                # ---------------------------------------------------------------------
                # Clustering
                # ---------------------------------------------------------------------
                # Perform K-means and calculate Shannon Entropy for each graph theory
                # measurement
                # TODO: think how you want to transform SM and DM into one single
                # matrix
                kmeans = KMeans(n_clusters=nclusters)
                graph_measures_labels = {}
                for key in graph_measures:
                    pdb.set_trace()
                    kmeans.fit_transform(graph_measures[key])
                    graph_measures_labels[key] = kmeans.labels_
                    graph_measures_labels[key + '_h'], graph_measures_labels[key + 's2'], \
                    graph_measures_labels['n_labels_gm'], \
                    graph_measures_labels['n_classes_gm'] = shanon_entropy(graph_measures_labels[key])

                pickle.dump(graph_measures_labels, open(os.path.join(output_basepath,
                                                                     grouping_type, network_type,
                                                                     'rand_ind_%02d' % rand_ind,
                                                                     '%s' % subject, '%02d_clusters' % nclusters,
                                                                     'graph_measures_labels_shannon_%s.pickle' % (
                                                                     subject)), 'wb'))

            # ---------------------------------------------------------------------
            # Clustering
            # ---------------------------------------------------------------------
            # Calculate the K-means clusters
            if graph_analysis == False:
                synchrony_bin_flat = np.zeros((hilbert_t_points, (synchrony_bin.shape[0]) ** 2))
                total_entropy = {}
                cluster_centroids = {}
                for t in range(hilbert_t_points):
                    synchrony_bin_flat[t, :] = np.ndarray.flatten(synchrony_bin[:, :, t])
                kmeans = KMeans(n_clusters=nclusters)
                kmeans.fit_transform(synchrony_bin_flat)
                kmeans_labels = kmeans.labels_
                centroids = kmeans.cluster_centers_
                cluster_centroids['centroids'] = centroids
                total_entropy['synchrony_h'], total_entropy['s2'], \
                total_entropy['n_labels_syn'], \
                total_entropy['n_classes_syn'] = shanon_entropy(kmeans_labels)
                pickle.dump(cluster_centroids, open(os.path.join(output_basepath,
                                                                 grouping_type, network_type,
                                                                 'rand_ind_%02d' % rand_ind,
                                                                 '%s' % subject, '%02d_clusters' % nclusters,
                                                                 '%s_cluster_centroid.pickle' % (subject)), 'wb'))
                pickle.dump(total_entropy, open(os.path.join(output_basepath,
                                                             grouping_type, network_type,
                                                             'rand_ind_%02d' % rand_ind,
                                                             '%s' % subject, '%02d_clusters' % nclusters,
                                                             '%s_total_shannon_entropy.pickle' % (subject)), 'wb'))

                # # save plot of centroids
                # n_centroids = centroids.shape[0]
                # for ncentroid in range(n_centroids):
                #     state = numpy.zeros((n_regions,n_regions))
                #     n_centroid = centroids[ncentroid]
                #     for ii in range(82):
                #         states[ii,:] = n_centroid[82*ii:82*(ii+1)]
                #         plt.imshow(states, interpolation='nearest')
                #         fig.savefig('state_%s.png' %ncentroid)
                #         plt.clf()

            print('Done!')
            print ('--------------------------------------------------------------')
