import pandas as pd
import numpy as np
from sklearn import linear_model, preprocessing
import matplotlib.pyplot as plt
from sklearn.metrics import r2_score, mean_squared_error
from sklearn.preprocessing import StandardScaler
import seaborn as sns
from scipy.spatial.distance import cdist
from sklearn.utils import resample
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.exceptions import ConvergenceWarning
import xgboost
from sklearn.decomposition import PCA
import warnings
import random
import time
import os
import sys
import argparse

# Ignore FutureWarnings and SettingWithCopyWarnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=ConvergenceWarning, module="sklearn.neural_network")
pd.options.mode.chained_assignment = None  # default='warn'

# Create the parser for the command line arguments
def create_parser():
    parser = argparse.ArgumentParser(description="Run experiments with different combinations of grid search variables.")
    parser.add_argument("--dataset_name", type=str, help="Name of the esm embeddings csv file of the dataset dataset")
    parser.add_argument("--base_path", type=str, help="Base path of the dataset, which should contain the esm embeddings and labels csv files")
    parser.add_argument("--num_simulations", type=int, help="Number of simulations for each parameter combination. Example: 3, 10")
    parser.add_argument("--num_iterations", type=int, nargs="+", help="List of number of iterations. Example: 3 5 10 (must be greater than 1)")
    parser.add_argument("--measured_var", type=str, nargs="+", help="Fitness type to train on. Options: fitness fitness_scaled")
    parser.add_argument("--learning_strategies", type=str, nargs="+", help="Type of learning strategy. Options: random top5bottom5 top10 dist")
    parser.add_argument("--num_mutants_per_round", type=int, nargs="+", help="Number of mutants per round. Example: 8 10 16 32 128")
    parser.add_argument("--embedding_types", type=str, nargs="+", help="Types of embeddings to train on. Options: embeddings embeddings_norm embeddings_pca")
    parser.add_argument("--regression_types", type=str, nargs="+", help="Regression types. Options: ridge lasso elasticnet linear neuralnet randomforest gradientboosting")
    return parser

# Function to read in the data
def read_data(dataset_name, base_path):
    # Construct the file paths
    embeddings_file = os.path.join(base_path, 'csvs', dataset_name + '.csv')
    labels_file = os.path.join(base_path, 'csvs', dataset_name.split('_')[0] + '_labels.csv')

    # Read in mean embeddings across all rounds
    embeddings = pd.read_csv(embeddings_file, index_col=0)

    # Read in labels
    labels = pd.read_csv(labels_file)

    # Filter out rows where fitness is NaN
    labels = labels[labels['fitness'].notna()]

    # Filter out rows in embeddings where row names are not in labels variant column
    embeddings = embeddings[embeddings.index.isin(labels['variant'])]

    # Align labels by variant
    labels = labels.sort_values(by=['variant'])

    # Align embeddings by row name
    embeddings = embeddings.sort_index()

    # Confirm that labels and embeddings are aligned, reset index
    labels = labels.reset_index(drop=True)

    # Get the variants in labels and embeddings, convert to list
    label_variants = labels['variant'].tolist()
    embedding_variants = embeddings.index.tolist()

    # Check if embedding row names and label variants are identical
    if label_variants == embedding_variants:
        print('Embeddings and labels are aligned')

    return embeddings, labels

# Function to scale the embeddings in the dataframe
def scale_embeddings(embeddings_df):
    
    scaler = StandardScaler()
    scaled_embeddings = scaler.fit_transform(embeddings_df)

    scaled_embeddings_df = pd.DataFrame(scaled_embeddings)

    return scaled_embeddings_df

# Perform PCA on the embeddings
def perform_pca(embeddings_df, labels_df, dataset_name, n_components=8):
    # Perform PCA on the embeddings
    pca = PCA(n_components=50)
    embeddings_pca = pca.fit_transform(embeddings_df)

    # Get the variance explained by each dimension
    variance_ratio = pca.explained_variance_ratio_

    # Plot the PCA embeddings
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))

    # Scatter plot of PCA embeddings
    sc = axes[0].scatter(embeddings_pca[:, 0], embeddings_pca[:, 1], c=labels_df["fitness_scaled"], cmap='viridis')
    cbar = plt.colorbar(sc, ax=axes[0])
    cbar.set_label('Fitness Z-score')
    axes[0].set_xlabel(f'PCA 1 ({variance_ratio[0]*100:.2f}% variance)')
    axes[0].set_ylabel(f'PCA 2 ({variance_ratio[1]*100:.2f}% variance)')
    axes[0].set_title('PCA Embeddings')

    cumulative_variance_ratio = np.cumsum(pca.explained_variance_ratio_)

    # Generate the elbow plot
    axes[1].plot(range(1, len(cumulative_variance_ratio) + 1), cumulative_variance_ratio, 'bo-')
    axes[1].set_xlabel('Number of Components')
    axes[1].set_ylabel('Cumulative Explained Variance Ratio')
    axes[1].set_title('Elbow Plot - Explained Variance vs. Number of Components')
    axes[1].set_xticks(range(1, len(cumulative_variance_ratio) + 1))
    axes[1].grid(True)

    plt.tight_layout()
    # save the plot
    fig.savefig('plots/' + dataset_name + '_pca.png')

    # Get the embeddings for the top n_components
    embeddings_pca = embeddings_pca[:, :n_components]
    # Convert embeddings to a dataframe
    embeddings_pca_df = pd.DataFrame(embeddings_pca, columns=[f'PCA {i}' for i in range(1, n_components + 1)])

    return embeddings_pca_df

# Active learning function for one iteration
def active_learner(iter_train, iter_test, embeddings_pd, labels_pd, measured_var, regression_type='ridge', top_n=None, final_round=10):
    # reset the indices of embeddings_pd and labels_pd
    embeddings_pd = embeddings_pd.reset_index(drop=True)
    labels_pd = labels_pd.reset_index(drop=True)

    # save column 'iteration' in the labels dataframe
    iteration = labels_pd['iteration']

    # save labels
    labels = labels_pd

    # save mean embeddings as numpy array
    a = embeddings_pd

    # subset a, y to only include the rows where iteration = iter_train and iter_test
    idx_train = iteration[iteration.isin(iter_train)].index.to_numpy()
    idx_test = iteration[iteration.isin([iter_test])].index.to_numpy()

    # subset a to only include the rows where iteration = iter_train and iter_test
    X_train = a.loc[idx_train, :]
    X_test = a.loc[idx_test, :]

    y_train = labels[iteration.isin(iter_train)][measured_var]
    y_train_fitness_scaled = labels[iteration.isin(iter_train)]['fitness_scaled']
    y_train_fitness_binary = labels[iteration.isin(iter_train)]['fitness_binary']

    y_test = labels[iteration.isin([iter_test])][measured_var]
    y_test_fitness_scaled = labels[iteration.isin([iter_test])]['fitness_scaled']
    y_test_fitness_binary = labels[iteration.isin([iter_test])]['fitness_binary']

    # fit
    if regression_type == 'ridge':
        model = linear_model.RidgeCV()
    elif regression_type == 'bayesianridge':
        model = linear_model.BayesianRidge()
    elif regression_type == 'lasso':
        model = linear_model.LassoCV(max_iter=100000,tol=1e-3)
    elif regression_type == 'elasticnet':
        model = linear_model.ElasticNetCV(max_iter=100000,tol=1e-3)
    elif regression_type == 'linear':
        model = linear_model.LinearRegression()
    elif regression_type == 'neuralnet':
        model = MLPRegressor(hidden_layer_sizes=(5), max_iter=1000, activation='relu', solver='adam', alpha=0.001,
                             batch_size='auto', learning_rate='constant', learning_rate_init=0.001, power_t=0.5,
                             momentum=0.9, nesterovs_momentum=True, shuffle=True, random_state=1, tol=0.0001,
                             verbose=False, warm_start=False, early_stopping=False, validation_fraction=0.1, beta_1=0.9,
                             beta_2=0.999, epsilon=1e-08)
    elif regression_type == 'randomforest':
        model = RandomForestRegressor(n_estimators=100, criterion='friedman_mse', max_depth=None, min_samples_split=2,
                                      min_samples_leaf=1, min_weight_fraction_leaf=0.0, max_features='auto',
                                      max_leaf_nodes=None, min_impurity_decrease=0.0, bootstrap=True, oob_score=False,
                                      n_jobs=None, random_state=1, verbose=0, warm_start=False, ccp_alpha=0.0,
                                      max_samples=None)
    elif regression_type == 'gradientboosting':
        model = xgboost.XGBRegressor(objective='reg:squarederror', colsample_bytree=0.3, learning_rate=0.1,
                                     max_depth=5, alpha=10, n_estimators=10)

    model.fit(X_train, y_train)

    # make predictions on test data
    y_pred_train = model.predict(X_train)
    y_std_train = np.zeros(len(y_pred_train))
    if regression_type != 'bayesianridge':
        y_pred_test = model.predict(X_test)
        y_std_test = np.zeros(len(y_pred_test))
    elif regression_type == 'bayesianridge':
        y_pred_test, y_std_test = model.predict(X_test, return_std=True)

    # calculate metrics
    train_error = mean_squared_error(y_train, y_pred_train)
    test_error = mean_squared_error(y_test, y_pred_test)
    # compute train and test r^2
    train_r_squared = r2_score(y_train, y_pred_train)
    test_r_squared = r2_score(y_test, y_pred_test)
    if regression_type == 'linear' or regression_type == 'neuralnet' or regression_type == 'randomforest' or regression_type == 'gradientboosting':
        alpha = 0
    else:
        alpha = model.alpha_
    dist_metric_train = cdist(X_train, X_test, metric='euclidean').min(axis=1)
    dist_metric_test = cdist(X_test, X_train, metric='euclidean').min(axis=1)

    # combine predicted and actual thermostability values with sequence IDs into a new dataframe
    df_train = pd.DataFrame({'variant': labels.variant[idx_train], 'y_pred': y_pred_train, 'y_actual': y_train, 
                             'y_actual_scaled': y_train_fitness_scaled, 'y_actual_binary': y_train_fitness_binary,
                             'dist_metric': dist_metric_train, 'std_predictions': y_std_train})
    df_test = pd.DataFrame({'variant': labels.variant[idx_test], 'y_pred': y_pred_test, 'y_actual': y_test, 
                            'y_actual_scaled': y_test_fitness_scaled, 'y_actual_binary': y_test_fitness_binary,
                            'dist_metric': dist_metric_test, 'std_predictions': y_std_test})
    df_all = pd.concat([df_train, df_test])

    df_sorted_all = df_all.sort_values('y_pred', ascending=False).reset_index(drop=True)

    # Calculate additional metrics
    median_fitness_scaled = df_sorted_all.loc[:final_round, 'y_actual_scaled'].median()
    top_fitness_scaled = df_sorted_all.loc[:final_round, 'y_actual_scaled'].max()
    fitness_binary_percentage = df_sorted_all.loc[:final_round, 'y_actual_binary'].mean()

    return train_error, test_error, train_r_squared, test_r_squared, alpha, median_fitness_scaled, top_fitness_scaled, fitness_binary_percentage, df_test

# Function to run simulations for a given set of parameters
def run_simulations(labels, embeddings, num_simulations, num_iterations, num_mutants_per_round=10, measured_var = 'fitness', regression_type='ridge', learning_strategy='top10', top_n=None, final_round = 10):
    output_list = []

    for i in range(num_simulations):

        random.seed(i)
        random_mutants = random.sample(list(labels.variant[labels.variant != 'WT']), num_mutants_per_round)
        iteration_one_ids = random_mutants
        iteration_one = pd.DataFrame({'variant': iteration_one_ids, 'iteration': 1})
        WT = pd.DataFrame({'variant': 'WT', 'iteration': 0}, index=[0])
        iteration_one = iteration_one.append(WT)
        labels_one = pd.merge(labels, iteration_one, on='variant', how='left')
        labels_one.iteration[labels_one.iteration.isnull()] = 1001

        test_error_list = []
        train_error_list = []
        train_r_squared_list = []
        test_r_squared_list = []
        alpha_list = []
        median_fitness_scaled_list = []
        top_fitness_scaled_list = []
        fitness_binary_percentage_list = []

        labels_new = labels_one
        iteration_new = iteration_one

        for j in range(2, num_iterations + 1):
            iteration_old = iteration_new

            train_error, test_error, train_r_squared, test_r_squared, alpha, median_fitness_scaled, top_fitness_scaled, fitness_binary_percentage, df_test_new = active_learner(
                iter_train=iteration_old['iteration'].unique().tolist(), iter_test=1001,
                embeddings_pd=embeddings, labels_pd=labels_new,
                measured_var=measured_var, regression_type=regression_type, top_n=top_n, final_round=final_round)

            test_error_list.append(test_error)
            train_error_list.append(train_error)
            train_r_squared_list.append(train_r_squared)
            test_r_squared_list.append(test_r_squared)
            alpha_list.append(alpha)
            median_fitness_scaled_list.append(median_fitness_scaled)
            top_fitness_scaled_list.append(top_fitness_scaled)
            fitness_binary_percentage_list.append(fitness_binary_percentage)

            if learning_strategy == 'dist':
                iteration_new_ids = df_test_new.sort_values(by='dist_metric', ascending=False).head(num_mutants_per_round).variant
            elif learning_strategy == 'dist_std':
                iteration_new_ids = df_test_new.sort_values(by='dist_metric', ascending=False).head(int(num_mutants_per_round/2)).variant
                iteration_new_ids.append(df_test_new.sort_values(by='std_predictions', ascending=False).head(int(num_mutants_per_round/2)).variant)
            elif learning_strategy == 'random':
                iteration_new_ids = random.sample(list(df_test_new.variant), num_mutants_per_round)
            elif learning_strategy == 'top5bottom5':
                iteration_new_ids = df_test_new.sort_values(by='y_pred', ascending=False).head(int(num_mutants_per_round/2)).variant
                iteration_new_ids.append(df_test_new.sort_values(by='y_pred', ascending=False).tail(int(num_mutants_per_round/2)).variant)
            elif learning_strategy == 'top10':
                iteration_new_ids = df_test_new.sort_values(by='y_pred', ascending=False).head(num_mutants_per_round).variant

            iteration_new = pd.DataFrame({'variant': iteration_new_ids, 'iteration': j})
            iteration_new = iteration_new.append(iteration_old)
            labels_new = pd.merge(labels, iteration_new, on='variant', how='left')
            labels_new.iteration[labels_new.iteration.isnull()] = 1001

        df_metrics = pd.DataFrame({'test_error': test_error_list, 'train_error': train_error_list,
                                   'train_r_squared': train_r_squared_list, 'test_r_squared': test_r_squared_list,
                                   'alpha': alpha_list, 'median_fitness_scaled': median_fitness_scaled_list,
                                   'top_fitness_scaled': top_fitness_scaled_list,
                                   'fitness_binary_percentage': fitness_binary_percentage_list})

        output_list.append(df_metrics)

    return output_list


# Function to calculate the mean and standard deviation of each metric across simulations for each learning strategy
def calculate_average_metrics(output_lists):
    # Concatenate all dataframes from different output lists
    combined_df = pd.concat(output_lists)
    
    # Calculate the mean and standard deviation of each metric across simulations for each learning strategy
    mean_metrics = combined_df.groupby(combined_df.index).mean()
    std_metrics = combined_df.groupby(combined_df.index).std()
    
    return mean_metrics, std_metrics

# Function to run the experiment with different combinations of parameters
def run_experiment(dataset_name, base_path, num_simulations, num_iterations, measured_var, learning_strategies,
                   num_mutants_per_round, embedding_types, regression_types):
    # read in dataset
    embeddings, labels = read_data(dataset_name, base_path)

    # scale embeddings
    embeddings_norm = scale_embeddings(embeddings)

    # generate embeddings_pca
    embeddings_pca = perform_pca(embeddings, labels, dataset_name, n_components=8)

    # save the embeddings in a list    
    embeddings_list = {
        'embeddings': embeddings,
        'embeddings_norm': embeddings_norm,
        'embeddings_pca': embeddings_pca
    }

    # save the labels in a list
    output_results = {}

    # Initialize the total combinations count
    total_combinations = 0 

    # Print the total number of combinations
    for strategy in learning_strategies:
        for var in measured_var:
            for iterations in num_iterations:
                for mutants_per_round in num_mutants_per_round:
                    if mutants_per_round == 128 and iterations != 3:
                        continue  # Skip other iterations when mutants_per_round is 128
                    for embedding_type in embedding_types:
                        for regression_type in regression_types:
                            total_combinations += 1

    # Print the corrected total_combinations count
    print(f"Total combinations: {total_combinations}")

    # Initialize the combination count
    combination_count = 0

    start_time = time.time()

    for strategy in learning_strategies:
        output_results[strategy] = {}
        for var in measured_var:
            output_results[strategy][var] = {}
            for iterations in num_iterations:
                output_results[strategy][var][iterations] = {}
                for mutants_per_round in num_mutants_per_round:
                    output_results[strategy][var][iterations][mutants_per_round] = {}
                    for embedding_type in embedding_types:
                        output_results[strategy][var][iterations][mutants_per_round][embedding_type] = {}
                        for regression_type in regression_types:
                            if mutants_per_round == 128 and iterations != 3:
                                continue  # Skip other iterations when mutants_per_round is 128
                            combination_count += 1
                            # print overall progress
                            print(
                                f"Progress: {combination_count}/{total_combinations} "
                                f"({(combination_count/total_combinations)*100:.2f}%)"
                            )

                            # run simulations for current combination of parameters
                            output_list = run_simulations(
                                labels=labels,
                                embeddings=embeddings_list[embedding_type],
                                num_simulations=num_simulations,
                                num_iterations=iterations,
                                num_mutants_per_round=mutants_per_round,
                                measured_var=var,
                                regression_type=regression_type,
                                learning_strategy=strategy,
                                final_round=mutants_per_round,
                            )
                            mean_metrics, std_metrics = calculate_average_metrics(output_list)
                            output_results[strategy][var][iterations][mutants_per_round][embedding_type][regression_type] = (mean_metrics, std_metrics)

    end_time = time.time()
    execution_time = end_time - start_time

    print(f"Total execution time: {execution_time:.2f} seconds")

    # Save the mean output across simulations for each combination of parameters
    rows = []
    # Iterate over the output_results dictionary and extract the desired information
    for strategy in learning_strategies:
        for var in measured_var:
            for iterations in num_iterations:
                for mutants_per_round in num_mutants_per_round:
                    if mutants_per_round == 128 and iterations != 3:
                        continue  # Skip other iterations when mutants_per_round is 128
                    for embedding_type in embedding_types:
                        for regression_type in regression_types:
                            # get the first and last values of the metrics
                            first_median_fitness_scaled = output_results[strategy][var][iterations][mutants_per_round][embedding_type][regression_type][0]['median_fitness_scaled'].iloc[0]
                            first_top_fitness_scaled = output_results[strategy][var][iterations][mutants_per_round][embedding_type][regression_type][0]['top_fitness_scaled'].iloc[0]
                            first_fitness_binary_percentage = output_results[strategy][var][iterations][mutants_per_round][embedding_type][regression_type][0]['fitness_binary_percentage'].iloc[0]                        
                            last_median_fitness_scaled = output_results[strategy][var][iterations][mutants_per_round][embedding_type][regression_type][0]['median_fitness_scaled'].iloc[-1]
                            last_top_fitness_scaled = output_results[strategy][var][iterations][mutants_per_round][embedding_type][regression_type][0]['top_fitness_scaled'].iloc[-1]                        
                            last_fitness_binary_percentage = output_results[strategy][var][iterations][mutants_per_round][embedding_type][regression_type][0]['fitness_binary_percentage'].iloc[-1]

                            # Create a new row with the experimental setup and the metric
                            new_row = {
                                'num_iterations': iterations,
                                'measured_var': var,
                                'learning_strategy': strategy,
                                'num_mutants_per_round': mutants_per_round,
                                'embedding_type': embedding_type,
                                'regression_type': regression_type,
                                'first_median_fitness_scaled': first_median_fitness_scaled,
                                'first_top_fitness_scaled': first_top_fitness_scaled,
                                'first_fitness_binary_percentage': first_fitness_binary_percentage,
                                'last_top_fitness_scaled': last_top_fitness_scaled,
                                'last_median_fitness_scaled': last_median_fitness_scaled,
                                'last_fitness_binary_percentage': last_fitness_binary_percentage,
                            }
                            # Append the new row to the list of rows
                            rows.append(new_row)

    # create a dataframe from the list of rows
    df_results = pd.DataFrame(rows)

    # calculate the change in the metrics
    df_results['change_median_fitness_scaled'] = df_results['last_median_fitness_scaled'] - df_results['first_median_fitness_scaled']
    df_results['change_top_fitness_scaled'] = df_results['last_top_fitness_scaled'] - df_results['first_top_fitness_scaled']
    df_results['change_fitness_binary_percentage'] = df_results['last_fitness_binary_percentage'] - df_results['first_fitness_binary_percentage']

    # save the dataframe to a csv file using the dataset_name
    df_results.to_csv(f"results/{dataset_name}_results.csv", index=False)

def main():
    parser = create_parser()
    args = parser.parse_args()

    run_experiment(
        args.dataset_name, args.base_path, args.num_simulations, args.num_iterations,
        args.measured_var, args.learning_strategies, args.num_mutants_per_round,
        args.embedding_types, args.regression_types
    )

if __name__ == "__main__":
    main()
