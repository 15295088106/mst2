from abc import *
from copy import copy
from config import *
from dataloader import *
from matplotlib.ticker import MaxNLocator
from tqdm import *
import numpy as np
import pandas as pd
from pathlib import Path
from collections import defaultdict
import torch.utils.data as data_utils
import matplotlib.pyplot as plt
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.covariance import EllipticEnvelope
from sklearn.ensemble import IsolationForest
from sklearn.svm import OneClassSVM
from mpl_toolkits.mplot3d import Axes3D
from warnings import simplefilter

simplefilter(action="ignore", category=FutureWarning)

plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False
from numba import jit


class AbstractDataset(metaclass=ABCMeta):
    def __init__(self, args, stats=None):
        self.house_indicies = args.house_indicies
        self.appliance_names = args.appliance_names
        self.normalize = args.normalize
        self.sampling = args.sampling
        self.cutoff = [args.cutoff[i]
                       for i in ['aggregate'] + self.appliance_names]

        self.threshold = [args.threshold[i] for i in self.appliance_names]
        self.min_on = [args.min_on[i] for i in self.appliance_names]
        self.min_off = [args.min_off[i] for i in self.appliance_names]

        self.val_size = args.validation_size
        self.window_size = args.window_size
        self.window_stride = args.window_stride

        self.x, self.y, self.index, self.tpd = self.load_data()
        self.status = self.compute_status(self.y)
        print('Appliance:', self.appliance_names)
        print('Sum of ons:', np.sum(self.status, axis=0))
        print('Total length:', self.status.shape[0])

        if stats is None:
            self.x_mean = np.mean(self.x, axis=0)
            self.x_std = np.std(self.x, axis=0)
            self.y_mean = np.mean(self.y, axis=0)
            self.y_std = np.std(self.y, axis=0)
        else:
            self.x_mean, self.x_std, self.y_mean, self.y_std = stats
            self.y_mean = np.mean(self.y, axis=0)
            self.y_std = np.std(self.y, axis=0)

        self.x = (self.x - self.x_mean) / self.x_std
        # self.y = (self.y - self.y_mean) / self.y_std

    @classmethod
    @abstractmethod
    def code(cls):
        pass

    @classmethod
    def raw_code(cls):
        return cls.code()

    @abstractmethod
    def load_data(self):
        pass

    def get_data(self):
        return self.x, self.y, self.status

    def get_original_data(self):
        x_org = self.x * self.x_std + self.x_mean
        return x_org, self.y, self.status

    def get_mean_std(self):
        return self.x_mean, self.x_std,self.y_mean, self.y_std

    def compute_status(self, data):
        status = np.zeros(data.shape)
        if len(data.squeeze().shape) == 1:
            columns = 1
        else:
            columns = data.squeeze().shape[-1]

        if not self.threshold:
            self.threshold = [10 for i in range(columns)]
        if not self.min_on:
            self.min_on = [1 for i in range(columns)]
        if not self.min_off:
            self.min_off = [1 for i in range(columns)]

        for i in range(columns):
            print(i)
            initial_status = data[:, i] >= self.threshold[i]
            status_diff = np.diff(initial_status)
            events_idx = status_diff.nonzero()

            events_idx = np.array(events_idx).squeeze()
            events_idx += 1

            if initial_status[0]:
                events_idx = np.insert(events_idx, 0, 0)

            if initial_status[-1]:
                events_idx = np.insert(
                    events_idx, events_idx.size, initial_status.size)

            events_idx = events_idx.reshape((-1, 2))
            on_events = events_idx[:, 0].copy()
            off_events = events_idx[:, 1].copy()
            assert len(on_events) == len(off_events)

            if len(on_events) > 0:
                off_duration = on_events[1:] - off_events[:-1]
                off_duration = np.insert(off_duration, 0, 1000)
                on_events = on_events[off_duration > self.min_off[i]]
                off_events = off_events[np.roll(
                    off_duration, -1) > self.min_off[i]]

                on_duration = off_events - on_events
                on_events = on_events[on_duration >= self.min_on[i]]
                off_events = off_events[on_duration >= self.min_on[i]]
                assert len(on_events) == len(off_events)

            temp_status = data[:, i].copy()
            temp_status[:] = 0
            for on, off in zip(on_events, off_events):
                temp_status[on: off] = 1
            status[:, i] = temp_status

        return status

    def get_status(self):
        return self.status

    def get_datasets(self):
        val_end = int(self.val_size * len(self.x))
        val = NILMDataset(self.x[:val_end], self.y[:val_end], self.status[:val_end], self.index[:val_end],
                          self.window_size, self.window_size)
        train = NILMDataset(self.x[val_end:], self.y[val_end:], self.status[val_end:], self.index[val_end:],
                            self.window_size, self.window_stride)
        return train, val

    def get_bert_datasets(self, mask_prob=0.25):
        val_end = int(self.val_size * len(self.x))
        val = NILMDataset(self.x[:val_end], self.y[:val_end], self.status[:val_end], self.index[:val_end],
                          self.window_size, self.window_size)
        train = BERTDataset(self.x[val_end:], self.y[val_end:], self.status[val_end:], self.index[val_end:],
                            self.window_size, self.window_stride, mask_prob=mask_prob)
        return train, val

    def _get_rawdata_root_path(self):
        return Path(RAW_DATASET_ROOT_FOLDER)

    def _get_folder_path(self):
        root = self._get_rawdata_root_path()
        return root.joinpath(self.raw_code())


class REDD_LF_Dataset(AbstractDataset):
    @classmethod
    def code(cls):
        return 'redd_lf'

    @classmethod
    def _if_data_exists(self):
        folder = Path(RAW_DATASET_ROOT_FOLDER).joinpath(self.code())
        first_file = folder.joinpath('house_1', 'channel_1.dat')
        if first_file.is_file():
            return True
        return False

    def load_data(self):
        for appliance in self.appliance_names:
            assert appliance in ['dishwasher',
                                 'refrigerator', 'microwave', 'washer_dryer']

        for house_id in self.house_indicies:
            assert house_id in [1, 2, 3, 4, 5, 6]

        if not self.cutoff:
            self.cutoff = [6000] * (len(self.appliance_names) + 1)

        if not self._if_data_exists():
            print('Please download, unzip and move data into',
                  self._get_folder_path())
            raise FileNotFoundError

        else:
            directory = self._get_folder_path()

            for house_id in self.house_indicies:
                house_folder = directory.joinpath('house_' + str(house_id))
                house_label = pd.read_csv(house_folder.joinpath(
                    'labels.dat'), sep=' ', header=None)

                main_1 = pd.read_csv(house_folder.joinpath(
                    'channel_1.dat'), sep=' ', header=None)
                main_2 = pd.read_csv(house_folder.joinpath(
                    'channel_2.dat'), sep=' ', header=None)
                house_data = pd.merge(main_1, main_2, how='inner', on=0)
                house_data.iloc[:, 1] = house_data.iloc[:,
                                        1] + house_data.iloc[:, 2]
                house_data = house_data.iloc[:, 0: 2]

                appliance_list = house_label.iloc[:, 1].values
                app_index_dict = defaultdict(list)

                for appliance in self.appliance_names:
                    data_found = False
                    for i in range(len(appliance_list)):
                        if appliance_list[i] == appliance:
                            app_index_dict[appliance].append(i + 1)
                            data_found = True

                    if not data_found:
                        app_index_dict[appliance].append(-1)

                if np.sum(list(app_index_dict.values())) == -len(self.appliance_names):
                    self.house_indicies.remove(house_id)
                    continue

                for appliance in self.appliance_names:
                    if app_index_dict[appliance][0] == -1:
                        temp_values = house_data.copy().iloc[:, 1]
                        temp_values[:] = 0
                        temp_data = house_data.copy().iloc[:, :2]
                        temp_data.iloc[:, 1] = temp_values
                    else:
                        temp_data = pd.read_csv(house_folder.joinpath(
                            'channel_' + str(app_index_dict[appliance][0]) + '.dat'), sep=' ', header=None)

                    if len(app_index_dict[appliance]) > 1:
                        for idx in app_index_dict[appliance][1:]:
                            temp_data_ = pd.read_csv(house_folder.joinpath(
                                'channel_' + str(idx) + '.dat'), sep=' ', header=None)
                            temp_data = pd.merge(
                                temp_data, temp_data_, how='inner', on=0)
                            temp_data.iloc[:, 1] = temp_data.iloc[:,
                                                   1] + temp_data.iloc[:, 2]
                            temp_data = temp_data.iloc[:, 0: 2]

                    house_data = pd.merge(
                        house_data, temp_data, how='inner', on=0)
                # house_data.loc[:, 3] = house_data.iloc[:, 0]
                house_data.iloc[:, 0] = pd.to_datetime(
                    house_data.iloc[:, 0], unit='s')
                house_data.columns = ['time', 'aggregate'] + \
                                     [i for i in self.appliance_names]
                house_data = house_data.set_index('time')
                house_data = house_data.resample(self.sampling).mean().fillna(
                    method='ffill', limit=30)
                # house_data=house_data.reset_index()
                # 截止此数据全部对齐

                if house_id == self.house_indicies[0]:
                    entire_data = house_data
                else:
                    entire_data = entire_data.append(
                        house_data, ignore_index=False)

                entire_data = entire_data.dropna()
                entire_data = entire_data[entire_data['aggregate'] > 0]
                entire_data[entire_data['aggregate'] < 5] = 0
                entire_data = entire_data.clip(
                    [0] * len(entire_data.columns), self.cutoff, axis=1)
                # ********测试开始
                # 时间分段状态概率
                # entire_data.index.normalize().drop('2011-04-23')
                # entire_data.index.normalize().drop('2011-04-24')
                # entire_data.index.normalize().drop('2011-04-30')
                # entire_data.index.normalize().drop('2011-05-1')
                house_data['only data'] = house_data.index.normalize()
                house_data['weekday'] = house_data.index.weekday + 1
                appliancedata = house_data[appliance]
                appliancedata.index = house_data.index
                # appliancedata = appliancedata[appliancedata[0] > 0]
                # appliancedata[appliancedata < 5] = 0
                # appliancedata = appliancedata.clip(
                #     [0] * len(appliancedata), self.cutoff, axis=0)
                appliancedatah1 = appliancedata.between_time("0:00:00", "5:00:00")
                tpd1 = appliancedatah1[appliancedatah1 > int(self.threshold[0])].count() / appliancedatah1.count()
                appliancedatah2 = appliancedata.between_time("5:00:00", "7:00:00")
                tpd2 = appliancedatah2[appliancedatah2 > int(self.threshold[0])].count() / appliancedatah2.count()
                appliancedatah3 = appliancedata.between_time("7:00:00", "9:00:00")
                tpd3 = appliancedatah3[appliancedatah3 > int(self.threshold[0])].count() / appliancedatah3.count()
                appliancedatah4 = appliancedata.between_time("9:00:00", "11:00:00")
                tpd4 = appliancedatah4[appliancedatah4 > int(self.threshold[0])].count() / appliancedatah4.count()
                appliancedatah5 = appliancedata.between_time("11:00:00", "13:00:00")
                tpd5 = appliancedatah5[appliancedatah5 > int(self.threshold[0])].count() / appliancedatah5.count()
                appliancedatah6 = appliancedata.between_time("13:00:00", "15:00:00")
                tpd6 = appliancedatah6[appliancedatah6 > int(self.threshold[0])].count() / appliancedatah6.count()
                appliancedatah7 = appliancedata.between_time("15:00:00", "17:00:00")
                tpd7 = appliancedatah7[appliancedatah7 > int(self.threshold[0])].count() / appliancedatah7.count()
                appliancedatah8 = appliancedata.between_time("17:00:00", "19:00:00")
                tpd8 = appliancedatah8[appliancedatah8 > int(self.threshold[0])].count() / appliancedatah8.count()
                appliancedatah9 = appliancedata.between_time("19:00:00", "21:00:00")
                tpd9 = appliancedatah9[appliancedatah9 > int(self.threshold[0])].count() / appliancedatah9.count()
                appliancedatah10 = appliancedata.between_time("21:00:00", "23:00:00")
                tpd10 = appliancedatah10[appliancedatah10 > int(self.threshold[0])].count() / appliancedatah10.count()
                appliancedatah11 = appliancedata.between_time("23:00:00", "0:00:00")
                tpd11 = appliancedatah11[appliancedatah11 > int(self.threshold[0])].count() / appliancedatah11.count()
                tpd = np.array([tpd1, tpd2, tpd3, tpd4, tpd5, tpd6, tpd7, tpd8, tpd9, tpd10, tpd11])
                print('house_id:', house_id, '    tpd :', tpd)
                # 时间分段状态概率程序结束
                # entire_data = entire_data.reset_index()

                # 测试时注释了
                # fig, ax = plt.subplots(figsize=(15, 10))
                # ax.plot(house_data.index, house_data['aggregate'], color='blue', label='aggregate')
                # ax.plot(house_data.index, house_data[appliance], color='green', label=appliance)
                # plt.xticks(fontsize=10)
                # plt.yticks(fontsize=10)
                # plt.legend()
                # plt.show()
                # 测试时注释结束

                # fig, ax = plt.subplots(figsize=(15, 10))
                # ax.plot(entire_data.index, entire_data['aggregate'], color='blue', label='aggregate')
                # ax.plot(entire_data.index, entire_data[appliance], color='green', label=appliance)
                # plt.xticks(fontsize=10)
                # plt.yticks(fontsize=10)
                # plt.legend()
                # plt.show()
                for i in range(1, 8):
                    app_power = house_data[house_data['weekday'] == i]
                    print(app_power.mean())
                    app_power1 = app_power.iloc[app_power.index.indexer_between_time("1:00:00", "5:00:00")].mean()
                    app_power2 = app_power.iloc[app_power.index.indexer_between_time("6:00:00", "7:00:00")].mean()
                    app_power3 = app_power.iloc[app_power.index.indexer_between_time("7:00:00", "9:00:00")].mean()
                    app_power4 = app_power.iloc[app_power.index.indexer_between_time("9:00:00", "11:00:00")].mean()
                    app_power5 = app_power.iloc[app_power.index.indexer_between_time("11:00:00", "13:00:00")].mean()
                    app_power6 = app_power.iloc[app_power.index.indexer_between_time("13:00:00", "15:00:00")].mean()
                    app_power7 = app_power.iloc[app_power.index.indexer_between_time("15:00:00", "17:00:00")].mean()
                    app_power8 = app_power.iloc[app_power.index.indexer_between_time("17:00:00", "19:00:00")].mean()
                    app_power9 = app_power.iloc[app_power.index.indexer_between_time("19:00:00", "21:00:00")].mean()
                    app_power10 = app_power.iloc[app_power.index.indexer_between_time("21:00:00", "23:00:00")].mean()
                    app_power11 = app_power.iloc[app_power.index.indexer_between_time("23:00:00", "1:00:00")].mean()
                    # print(app_power1,app_power2,app_power3,app_power4,app_power5,app_power6,app_power7,app_power8,app_power9,app_power10,app_power11)
                    if i == 1 and house_id == self.house_indicies[0]:
                        appm_power = pd.DataFrame(
                            [app_power1, app_power2, app_power3, app_power4, app_power5, app_power6, app_power7,
                             app_power8, app_power9, app_power10, app_power11])
                    else:
                        appm_power = appm_power.append(pd.DataFrame(
                            [app_power1, app_power2, app_power3, app_power4, app_power5, app_power6, app_power7,
                             app_power8, app_power9, app_power10, app_power11]))
                # print(appm_power.shape)
                # appm_power = appm_power.reset_index()
            #     if house_id == self.house_indicies[0]:
            #         appmm_power = appm_power
            #         house_index=0
            #     else:
            #         house_index=house_index+1
            #         for i in range(7):
            #             for j in range(11):
            #                 appmm_power.iloc[i*11+j, :] = appm_power.iloc[i * 11 + j + 77 * house_index, :] + appmm_power.iloc[i * 11 + j, :]
            #     print('appmm_power.shape()')
            # appmm_power = appmm_power/(house_index+1)
            plt.bar(range(len(appm_power)), appm_power[appliance], fc='bisque')
            plt.bar(range(len(appm_power)), appm_power['aggregate'], bottom=appm_power[appliance], fc='lavenderblush')
            plt.plot(range(len(appm_power)), appm_power[appliance], "r")
            plt.plot(range(len(appm_power)), appm_power['aggregate'], "r")
            plt.show()

            appliancedata = entire_data[appliance]
            appliancedata.index = entire_data.index
            appliancedatah1 = appliancedata.between_time("0:00:00", "5:00:00")
            tpd1 = appliancedatah1[appliancedatah1 > int(self.threshold[0])].count() / appliancedatah1.count()
            appliancedatah2 = appliancedata.between_time("5:00:00", "7:00:00")
            tpd2 = appliancedatah2[appliancedatah2 > int(self.threshold[0])].count() / appliancedatah2.count()
            appliancedatah3 = appliancedata.between_time("7:00:00", "9:00:00")
            tpd3 = appliancedatah3[appliancedatah3 > int(self.threshold[0])].count() / appliancedatah3.count()
            appliancedatah4 = appliancedata.between_time("9:00:00", "11:00:00")
            tpd4 = appliancedatah4[appliancedatah4 > int(self.threshold[0])].count() / appliancedatah4.count()
            appliancedatah5 = appliancedata.between_time("11:00:00", "13:00:00")
            tpd5 = appliancedatah5[appliancedatah5 > int(self.threshold[0])].count() / appliancedatah5.count()
            appliancedatah6 = appliancedata.between_time("13:00:00", "15:00:00")
            tpd6 = appliancedatah6[appliancedatah6 > int(self.threshold[0])].count() / appliancedatah6.count()
            appliancedatah7 = appliancedata.between_time("15:00:00", "17:00:00")
            tpd7 = appliancedatah7[appliancedatah7 > int(self.threshold[0])].count() / appliancedatah7.count()
            appliancedatah8 = appliancedata.between_time("17:00:00", "19:00:00")
            tpd8 = appliancedatah8[appliancedatah8 > int(self.threshold[0])].count() / appliancedatah8.count()
            appliancedatah9 = appliancedata.between_time("19:00:00", "21:00:00")
            tpd9 = appliancedatah9[appliancedatah9 > int(self.threshold[0])].count() / appliancedatah9.count()
            appliancedatah10 = appliancedata.between_time("21:00:00", "23:00:00")
            tpd10 = appliancedatah10[appliancedatah10 > int(self.threshold[0])].count() / appliancedatah10.count()
            appliancedatah11 = appliancedata.between_time("23:00:00", "0:00:00")
            tpd11 = appliancedatah11[appliancedatah11 > int(self.threshold[0])].count() / appliancedatah11.count()
            tpd = np.array([tpd1, tpd2, tpd3, tpd4, tpd5, tpd6, tpd7, tpd8, tpd9, tpd10, tpd11])
            print('tpd entire_data:', tpd)
            entire_data = entire_data.reset_index()

            # 测试时注释
            fig, ax = plt.subplots(figsize=(15, 10))
            ax.plot(entire_data.index, entire_data['aggregate'], color='blue', label='aggregate')
            ax.plot(entire_data.index, entire_data[appliance], color='green', label=appliance)
            plt.xticks(fontsize=10)
            plt.yticks(fontsize=10)
            plt.legend()
            plt.show()
            # 测试时注释结束
            # np.savetxt("XXX.csv", house_data[appliance], delimiter=",")
            # data = entire_data[['index','aggregate']]
            # data = entire_data[['aggregate']]
            # data = entire_data[[appliance]]
            # n_cluster = range(1, 20)
            # kmeans = [KMeans(n_clusters=i).fit(data) for i in n_cluster]
            # scores = [kmeans[i].score(data) for i in range(len(kmeans))]
            #
            # fig, ax = plt.subplots(figsize=(15, 10))
            # ax.plot(n_cluster, scores)
            # plt.xlabel('聚类集群数', fontsize=25)
            # plt.xticks(fontsize=25)
            # plt.ylabel('分数', fontsize=25)
            # plt.yticks(fontsize=25)
            # plt.title('Elbow 曲线', fontsize=25)
            # plt.gca().xaxis.set_major_locator(MaxNLocator(integer=True))
            # plt.grid(ls=":", c='b', )  # 打开坐标网格
            # plt.axhline(y=0, ls=":", c="yellow")  # 添加水平直线
            # plt.axvline(x=7, ls="-", c="green")  # 添加垂直直线
            # plt.show()
            # fig.savefig('1.svg', format='svg')  # 输出
            # kmeans = [KMeans(n_clusters=i).fit(data) for i in n_cluster]
            # entire_data['cluster'] = kmeans[4].predict(data)
            # entire_data.index = data.index
            #
            # # colors = {0: 'blue', 1: 'red'}
            # # fig, ax = plt.subplots(figsize=(10, 6))
            # # a = entire_data.loc[entire_data['cluster'] == 1, ['aggregate']]  # anomaly
            # # ax.plot(entire_data['aggregate'].index,entire_data['aggregate'], color='blue', label='正常值')
            # # ax.scatter(a['aggregate'].index,a['aggregate'], color='red', label='异常值')
            # # plt.show()
            # # @jit()
            # def getDistanceByPoint(data, model):
            #     distance = pd.Series([], dtype='float64')
            #     # for i in tqdm(range(0, len(data))):
            #     for i in tqdm(range(0, 30000)):
            #         Xa = np.array(data.iloc[i])
            #         Xb = model.cluster_centers_[model.labels_[i]]
            #         Xc = (Xa - Xb) / Xb
            #         distance._set_value(i, (np.linalg.norm(Xc)))
            #     return distance
            #
            # entire_data['anomaly1'] = 0
            # entire_data['salary_diff'] = entire_data['aggregate'].diff()
            # np.abs(entire_data['salary_diff'])
            # # entire_data['aggregate'] = np.convolve(entire_data['aggregate'], np.ones((50,))/50, mode="valid")
            # outliers_fraction = 0.01
            # # for i in tqdm(range(5, len(entire_data['aggregate'])-5,5)):
            # # for i in tqdm(range(5, 100000)):
            # #     # if (entire_data['cluster'].loc[i - 2] == entire_data['cluster'].loc[i - 1]) != entire_data['cluster'].loc[i] and entire_data['cluster'].loc[i] != (entire_data['cluster'].loc[i + 2] == entire_data['cluster'].loc[i + 1]):
            # #     #     entire_data.__getitem__('anomaly1').__setitem__(i, 1)
            # #     # elif (entire_data['salary_diff'].loc[i] > 50 and entire_data['cluster'].loc[i]!=entire_data['cluster'].loc[i+1]!=entire_data['cluster'].loc[i+2]!=entire_data['cluster'].loc[i-1]!=entire_data['cluster'].loc[i-2]):
            # #     #     entire_data.__getitem__('anomaly1').__setitem__(i, 1)
            # #     if np.abs(entire_data['salary_diff'].loc[i] - np.mean(entire_data['salary_diff'].loc[i - 5:i + 5]))   >  3*np.std(entire_data['salary_diff'].loc[i - 5:i + 5]):
            # #         entire_data.__getitem__('anomaly1').__setitem__(i, 1)
            #
            # # entire_data1=np.lib.stride_tricks.sliding_window_view(entire_data['aggregate'],10)#做一个滑动时间窗
            # # 得到每个点到取聚类中心的距离，我们设置了4个聚类中心，kmeans[3]表示有4个聚类中心的模型
            # distance = getDistanceByPoint(data, kmeans[7])
            # # 根据异常值比例outliers_fraction计算异常值的数量
            # number_of_outliers = int(outliers_fraction * len(distance))
            #
            # # 设定异常值的阈值
            # threshold = distance.nlargest(number_of_outliers).min()
            #
            # # 根据阈值来判断是否为异常值
            # entire_data['anomaly1'] = (distance >= threshold).astype(int)
            # # fig, ax = plt.subplots(figsize=(10, 6))
            # colors = {0: 'blue', 1: 'red'}
            # # ax.scatter(entire_data['principal_feature1'], entire_data['principal_feature2'],
            # #            c=entire_data["anomaly1"].apply(lambda x: colors[x]))
            # # plt.xlabel('principal feature1')
            # # plt.ylabel('principal feature2')
            # # plt.show()
            # print(entire_data.anomaly1.value_counts())
            #
            # fig, ax = plt.subplots(figsize=(15, 10))
            # a = entire_data.loc[entire_data['anomaly1'] == 1, ['aggregate']]  # anomaly
            #
            # ax.plot(entire_data['aggregate'], color='blue', label='正常值')
            # ax.scatter(a['aggregate'].index, a['aggregate'], color='red', label='异常值')
            # plt.xlabel('times', fontsize=25)
            # plt.xticks(fontsize=25)
            # plt.yticks(fontsize=25)
            # plt.ylabel('power', fontsize=25)
            # plt.legend()
            # plt.show()
            # fig.savefig('2.svg', format='svg')  # 输出
            # a.to_csv('异常值.csv')  # 存储到CSV中
            # entire_data.to_csv('entire_data值.csv')
            # entire_data.loc[entire_data['anomaly1'] == 1, ['aggregate']] = np.nan
            # # entire_data.interpolate()
            # entire_data['aggregate'] = entire_data['aggregate'].fillna(method='backfill')
            # entire_data.to_csv('修改entire_data值.csv')
            return entire_data.values[:, 1].astype(np.float64), entire_data.values[:, 2].reshape(-1, 1).astype(
                np.float64), entire_data.time, tpd


class UK_DALE_Dataset(AbstractDataset):
    @classmethod
    def code(cls):
        return 'uk_dale'

    @classmethod
    def _if_data_exists(self):
        folder = Path(RAW_DATASET_ROOT_FOLDER).joinpath(self.code())
        first_file = folder.joinpath('house_1', 'channel_1.dat')
        if first_file.is_file():
            return True
        return False

    def load_data(self):
        for appliance in self.appliance_names:
            assert appliance in ['dishwasher', 'fridge',
                                 'microwave', 'washing_machine', 'kettle']

        for house_id in self.house_indicies:
            assert house_id in [1, 2, 3, 4, 5]

        if not self.cutoff:
            self.cutoff = [6000] * (len(self.appliance_names) + 1)

        if not self._if_data_exists():
            print('Please download, unzip and move data into',
                  self._get_folder_path())
            raise FileNotFoundError

        else:
            directory = self._get_folder_path()

            for house_id in self.house_indicies:
                house_folder = directory.joinpath('house_' + str(house_id))
                house_label = pd.read_csv(house_folder.joinpath(
                    'labels.dat'), sep=' ', header=None)

                house_data = pd.read_csv(house_folder.joinpath(
                    'channel_1.dat'), sep=' ', header=None)
                house_data.iloc[:, 0] = pd.to_datetime(
                    house_data.iloc[:, 0], unit='s')
                house_data.columns = ['time', 'aggregate']
                house_data = house_data.set_index('time')
                house_data = house_data.resample(self.sampling).mean().fillna(
                    method='ffill', limit=30)

                appliance_list = house_label.iloc[:, 1].values
                app_index_dict = defaultdict(list)

                for appliance in self.appliance_names:
                    data_found = False
                    for i in range(len(appliance_list)):
                        if appliance_list[i] == appliance:
                            app_index_dict[appliance].append(i + 1)
                            data_found = True

                    if not data_found:
                        app_index_dict[appliance].append(-1)

                if np.sum(list(app_index_dict.values())) == -len(self.appliance_names):
                    self.house_indicies.remove(house_id)
                    continue

                for appliance in self.appliance_names:
                    if app_index_dict[appliance][0] == -1:
                        house_data.insert(len(house_data.columns), appliance, np.zeros(len(house_data)))
                    else:
                        temp_data = pd.read_csv(house_folder.joinpath(
                            'channel_' + str(app_index_dict[appliance][0]) + '.dat'), sep=' ', header=None)
                        temp_data.iloc[:, 0] = pd.to_datetime(
                            temp_data.iloc[:, 0], unit='s')
                        temp_data.columns = ['time', appliance]
                        temp_data = temp_data.set_index('time')
                        temp_data = temp_data.resample(self.sampling).mean().fillna(
                            method='ffill', limit=30)
                        house_data = pd.merge(
                            house_data, temp_data, how='inner', on='time')

                if house_id == self.house_indicies[0]:
                    entire_data = house_data
                    # if len(self.house_indicies) == 1:
                    #     entire_data = entire_data.reset_index(drop=True)
                else:
                    entire_data = entire_data.append(
                        # house_data, ignore_index=True)
                        house_data, ignore_index=False)

                # entire_data = entire_data.dropna().copy()
                entire_data = entire_data[entire_data['aggregate'] > 0]
                entire_data[entire_data < 5] = 0
                entire_data = entire_data.clip(
                    [0] * len(entire_data.columns), self.cutoff, axis=1)

                house_data['only data'] = house_data.index.normalize()
                house_data['weekday'] = house_data.index.weekday + 1
                appliancedata = house_data[appliance]
                appliancedata.index = house_data.index
                result = appliancedata.resample('1h').mean()#求每一个小时的数据均值
                # appliancedata = appliancedata[appliancedata[0] > 0]
                # appliancedata[appliancedata < 5] = 0
                # appliancedata = appliancedata.clip(
                #     [0] * len(appliancedata), self.cutoff, axis=0)
                appliancedatah1 = appliancedata.between_time("0:00:00", "5:00:00")
                tpd1 = appliancedatah1[appliancedatah1 > int(self.threshold[0])].count() / appliancedatah1.count()
                appliancedatah2 = appliancedata.between_time("5:00:00", "7:00:00")
                tpd2 = appliancedatah2[appliancedatah2 > int(self.threshold[0])].count() / appliancedatah2.count()
                appliancedatah3 = appliancedata.between_time("7:00:00", "9:00:00")
                tpd3 = appliancedatah3[appliancedatah3 > int(self.threshold[0])].count() / appliancedatah3.count()
                appliancedatah4 = appliancedata.between_time("9:00:00", "11:00:00")
                tpd4 = appliancedatah4[appliancedatah4 > int(self.threshold[0])].count() / appliancedatah4.count()
                appliancedatah5 = appliancedata.between_time("11:00:00", "13:00:00")
                tpd5 = appliancedatah5[appliancedatah5 > int(self.threshold[0])].count() / appliancedatah5.count()
                appliancedatah6 = appliancedata.between_time("13:00:00", "15:00:00")
                tpd6 = appliancedatah6[appliancedatah6 > int(self.threshold[0])].count() / appliancedatah6.count()
                appliancedatah7 = appliancedata.between_time("15:00:00", "17:00:00")
                tpd7 = appliancedatah7[appliancedatah7 > int(self.threshold[0])].count() / appliancedatah7.count()
                appliancedatah8 = appliancedata.between_time("17:00:00", "19:00:00")
                tpd8 = appliancedatah8[appliancedatah8 > int(self.threshold[0])].count() / appliancedatah8.count()
                appliancedatah9 = appliancedata.between_time("19:00:00", "21:00:00")
                tpd9 = appliancedatah9[appliancedatah9 > int(self.threshold[0])].count() / appliancedatah9.count()
                appliancedatah10 = appliancedata.between_time("21:00:00", "23:00:00")
                tpd10 = appliancedatah10[appliancedatah10 > int(self.threshold[0])].count() / appliancedatah10.count()
                appliancedatah11 = appliancedata.between_time("23:00:00", "0:00:00")
                tpd11 = appliancedatah11[appliancedatah11 > int(self.threshold[0])].count() / appliancedatah11.count()
                tpd = np.array([tpd1, tpd2, tpd3, tpd4, tpd5, tpd6, tpd7, tpd8, tpd9, tpd10, tpd11])
                print('house_id:', house_id, '    tpd :', tpd)
                # 时间分段状态概率程序结束
                # entire_data = entire_data.reset_index()
                fig, ax = plt.subplots(figsize=(15, 10))
                plt.title(house_id)
                ax.plot(house_data.index, house_data['aggregate'], color='blue', label='aggregate')
                ax.plot(house_data.index, house_data[appliance], color='green', label=appliance)
                plt.xticks(fontsize=10)
                plt.yticks(fontsize=10)
                plt.legend()
                plt.show()
                # fig, ax = plt.subplots(figsize=(15, 10))
                # ax.plot(entire_data.index, entire_data['aggregate'], color='blue', label='aggregate')
                # ax.plot(entire_data.index, entire_data[appliance], color='green', label=appliance)
                # plt.xticks(fontsize=10)
                # plt.yticks(fontsize=10)
                # plt.legend()
                # plt.show()
                for i in range(1, 8):
                    app_power = house_data[house_data['weekday'] == i]
                    if i == 1: print('单个房间每一天的平均功耗')
                    print(app_power.mean())
                    app_power1 = app_power.iloc[app_power.index.indexer_between_time("0:00:00", "1:00:00")].mean()
                    app_power2 = app_power.iloc[app_power.index.indexer_between_time("1:00:00", "2:00:00")].mean()
                    app_power3 = app_power.iloc[app_power.index.indexer_between_time("2:00:00", "3:00:00")].mean()
                    app_power4 = app_power.iloc[app_power.index.indexer_between_time("3:00:00", "4:00:00")].mean()
                    app_power5 = app_power.iloc[app_power.index.indexer_between_time("4:00:00", "5:00:00")].mean()
                    app_power6 = app_power.iloc[app_power.index.indexer_between_time("5:00:00", "6:00:00")].mean()
                    app_power7 = app_power.iloc[app_power.index.indexer_between_time("6:00:00", "7:00:00")].mean()
                    app_power8 = app_power.iloc[app_power.index.indexer_between_time("7:00:00", "8:00:00")].mean()
                    app_power9 = app_power.iloc[app_power.index.indexer_between_time("8:00:00", "9:00:00")].mean()
                    app_power10 = app_power.iloc[app_power.index.indexer_between_time("9:00:00", "10:00:00")].mean()
                    app_power11 = app_power.iloc[app_power.index.indexer_between_time("10:00:00", "11:00:00")].mean()
                    app_power12 = app_power.iloc[app_power.index.indexer_between_time("11:00:00", "12:00:00")].mean()
                    app_power13 = app_power.iloc[app_power.index.indexer_between_time("12:00:00", "13:00:00")].mean()
                    app_power14 = app_power.iloc[app_power.index.indexer_between_time("13:00:00", "14:00:00")].mean()
                    app_power15 = app_power.iloc[app_power.index.indexer_between_time("14:00:00", "15:00:00")].mean()
                    app_power16 = app_power.iloc[app_power.index.indexer_between_time("15:00:00", "16:00:00")].mean()
                    app_power17 = app_power.iloc[app_power.index.indexer_between_time("16:00:00", "17:00:00")].mean()
                    app_power18 = app_power.iloc[app_power.index.indexer_between_time("17:00:00", "18:00:00")].mean()
                    app_power19 = app_power.iloc[app_power.index.indexer_between_time("18:00:00", "19:00:00")].mean()
                    app_power20 = app_power.iloc[app_power.index.indexer_between_time("19:00:00", "20:00:00")].mean()
                    app_power21 = app_power.iloc[app_power.index.indexer_between_time("20:00:00", "21:00:00")].mean()
                    app_power22 = app_power.iloc[app_power.index.indexer_between_time("21:00:00", "22:00:00")].mean()
                    app_power23 = app_power.iloc[app_power.index.indexer_between_time("22:00:00", "23:00:00")].mean()
                    app_power24 = app_power.iloc[app_power.index.indexer_between_time("23:00:00", "23:59:59")].mean()

                    # print(app_power1,app_power2,app_power3,app_power4,app_power5,app_power6,app_power7,app_power8,app_power9,app_power10,app_power11)
                    if i == 1 and house_id == self.house_indicies[0]:
                        appm_power = pd.DataFrame(
                            [app_power1, app_power2, app_power3, app_power4, app_power5, app_power6, app_power7,
                             app_power8, app_power9, app_power10, app_power11, app_power12, app_power13, app_power14,
                             app_power15, app_power16, app_power17, app_power18, app_power19, app_power20, app_power21,
                             app_power22, app_power23, app_power24])
                    else:
                        appm_power = appm_power.append(pd.DataFrame(
                            [app_power1, app_power2, app_power3, app_power4, app_power5, app_power6, app_power7,
                             app_power8, app_power9, app_power10, app_power11, app_power12, app_power13, app_power14,
                             app_power15, app_power16, app_power17, app_power18, app_power19, app_power20, app_power21,
                             app_power22, app_power23, app_power24]))
                # print(appm_power.shape)
                # appm_power = appm_power.reset_index()
                #     if house_id == self.house_indicies[0]:
                #         appmm_power = appm_power
                #         house_index=0
                #     else:
                #         house_index=house_index+1
                #         for i in range(7):
                #             for j in range(11):
                #                 appmm_power.iloc[i*11+j, :] = appm_power.iloc[i * 11 + j + 77 * house_index, :] + appmm_power.iloc[i * 11 + j, :]
                #     print('appmm_power.shape()')
                # appmm_power = appmm_power/(house_index+1)
            plt.bar(range(len(appm_power)), appm_power[appliance], fc='bisque')
            plt.bar(range(len(appm_power)), appm_power['aggregate'], bottom=appm_power[appliance], fc='lavenderblush')
            plt.plot(range(len(appm_power)), appm_power[appliance], "r")
            plt.plot(range(len(appm_power)), appm_power['aggregate'], "r")
            plt.show()

            appliancedata = entire_data[appliance]
            appliancedata.index = entire_data.index
            appliancedatah1 = appliancedata.between_time("0:00:00", "5:00:00")
            tpd1 = appliancedatah1[appliancedatah1 > int(self.threshold[0])].count() / appliancedatah1.count()
            appliancedatah2 = appliancedata.between_time("5:00:00", "7:00:00")
            tpd2 = appliancedatah2[appliancedatah2 > int(self.threshold[0])].count() / appliancedatah2.count()
            appliancedatah3 = appliancedata.between_time("7:00:00", "9:00:00")
            tpd3 = appliancedatah3[appliancedatah3 > int(self.threshold[0])].count() / appliancedatah3.count()
            appliancedatah4 = appliancedata.between_time("9:00:00", "11:00:00")
            tpd4 = appliancedatah4[appliancedatah4 > int(self.threshold[0])].count() / appliancedatah4.count()
            appliancedatah5 = appliancedata.between_time("11:00:00", "13:00:00")
            tpd5 = appliancedatah5[appliancedatah5 > int(self.threshold[0])].count() / appliancedatah5.count()
            appliancedatah6 = appliancedata.between_time("13:00:00", "15:00:00")
            tpd6 = appliancedatah6[appliancedatah6 > int(self.threshold[0])].count() / appliancedatah6.count()
            appliancedatah7 = appliancedata.between_time("15:00:00", "17:00:00")
            tpd7 = appliancedatah7[appliancedatah7 > int(self.threshold[0])].count() / appliancedatah7.count()
            appliancedatah8 = appliancedata.between_time("17:00:00", "19:00:00")
            tpd8 = appliancedatah8[appliancedatah8 > int(self.threshold[0])].count() / appliancedatah8.count()
            appliancedatah9 = appliancedata.between_time("19:00:00", "21:00:00")
            tpd9 = appliancedatah9[appliancedatah9 > int(self.threshold[0])].count() / appliancedatah9.count()
            appliancedatah10 = appliancedata.between_time("21:00:00", "23:00:00")
            tpd10 = appliancedatah10[appliancedatah10 > int(self.threshold[0])].count() / appliancedatah10.count()
            appliancedatah11 = appliancedata.between_time("23:00:00", "0:00:00")
            tpd11 = appliancedatah11[appliancedatah11 > int(self.threshold[0])].count() / appliancedatah11.count()
            tpd = np.array([tpd1, tpd2, tpd3, tpd4, tpd5, tpd6, tpd7, tpd8, tpd9, tpd10, tpd11])
            print('tpd entire_data:', tpd)
            entire_data = entire_data.reset_index()
            fig, ax = plt.subplots(figsize=(15, 10))
            ax.plot(entire_data.index, entire_data['aggregate'], color='blue', label='aggregate')
            ax.plot(entire_data.index, entire_data[appliance], color='green', label=appliance)
            plt.xticks(fontsize=10)
            plt.yticks(fontsize=10)
            plt.legend()
            plt.show()

            # data = entire_data[['index', 'aggregate']]
            # data = entire_data[['aggregate']]
            entire_data = entire_data.dropna()
            # data = entire_data[[appliance]]
            # n_cluster = range(1, 20)
            # kmeans = [KMeans(n_clusters=i).fit(data) for i in n_cluster]
            # scores = [kmeans[i].score(data) for i in range(len(kmeans))]
            #
            # fig, ax = plt.subplots(figsize=(15, 10))
            # ax.plot(n_cluster, scores)
            # plt.xlabel('聚类集群数', fontsize=25)
            # plt.xticks(fontsize=25)
            # plt.ylabel('分数', fontsize=25)
            # plt.yticks(fontsize=25)
            # plt.title('Elbow 曲线', fontsize=25)
            # plt.gca().xaxis.set_major_locator(MaxNLocator(integer=True))
            # plt.grid(ls=":", c='b', )  # 打开坐标网格
            # plt.axhline(y=0, ls=":", c="yellow")  # 添加水平直线
            # plt.axvline(x=7, ls="-", c="green")  # 添加垂直直线
            # plt.show()
            # fig.savefig('1.svg', format='svg')  # 输出
            # kmeans = [KMeans(n_clusters=i).fit(data) for i in n_cluster]
            # entire_data['cluster'] = kmeans[4].predict(data)
            # entire_data.index = data.index
            #
            # # colors = {0: 'blue', 1: 'red'}
            # # fig, ax = plt.subplots(figsize=(10, 6))
            # # a = entire_data.loc[entire_data['cluster'] == 1, ['aggregate']]  # anomaly
            # # ax.plot(entire_data['aggregate'].index,entire_data['aggregate'], color='blue', label='正常值')
            # # ax.scatter(a['aggregate'].index,a['aggregate'], color='red', label='异常值')
            # # plt.show()
            # # @jit()
            # def getDistanceByPoint(data, model):
            #     distance = pd.Series([], dtype='float64')
            #     # for i in tqdm(range(0, len(data))):
            #     for i in tqdm(range(0, 30000)):
            #         Xa = np.array(data.iloc[i])
            #         Xb = model.cluster_centers_[model.labels_[i]]
            #         Xc = (Xa - Xb) / Xb
            #         distance._set_value(i, (np.linalg.norm(Xc)))
            #     return distance
            #
            # entire_data['anomaly1'] = 0
            # entire_data['salary_diff'] = entire_data['aggregate'].diff()
            # np.abs(entire_data['salary_diff'])
            # # entire_data['aggregate'] = np.convolve(entire_data['aggregate'], np.ones((50,))/50, mode="valid")
            # outliers_fraction = 0.01
            # # for i in tqdm(range(5, len(entire_data['aggregate'])-5,5)):
            # # for i in tqdm(range(5, 100000)):
            # #     # if (entire_data['cluster'].loc[i - 2] == entire_data['cluster'].loc[i - 1]) != entire_data['cluster'].loc[i] and entire_data['cluster'].loc[i] != (entire_data['cluster'].loc[i + 2] == entire_data['cluster'].loc[i + 1]):
            # #     #     entire_data.__getitem__('anomaly1').__setitem__(i, 1)
            # #     # elif (entire_data['salary_diff'].loc[i] > 50 and entire_data['cluster'].loc[i]!=entire_data['cluster'].loc[i+1]!=entire_data['cluster'].loc[i+2]!=entire_data['cluster'].loc[i-1]!=entire_data['cluster'].loc[i-2]):
            # #     #     entire_data.__getitem__('anomaly1').__setitem__(i, 1)
            # #     if np.abs(entire_data['salary_diff'].loc[i] - np.mean(entire_data['salary_diff'].loc[i - 5:i + 5]))   >  3*np.std(entire_data['salary_diff'].loc[i - 5:i + 5]):
            # #         entire_data.__getitem__('anomaly1').__setitem__(i, 1)
            #
            # # entire_data1=np.lib.stride_tricks.sliding_window_view(entire_data['aggregate'],10)#做一个滑动时间窗
            # # 得到每个点到取聚类中心的距离，我们设置了4个聚类中心，kmeans[3]表示有4个聚类中心的模型
            # distance = getDistanceByPoint(data, kmeans[7])
            # # 根据异常值比例outliers_fraction计算异常值的数量
            # number_of_outliers = int(outliers_fraction * len(distance))
            #
            # # 设定异常值的阈值
            # threshold = distance.nlargest(number_of_outliers).min()
            #
            # # 根据阈值来判断是否为异常值
            # entire_data['anomaly1'] = (distance >= threshold).astype(int)
            # # fig, ax = plt.subplots(figsize=(10, 6))
            # colors = {0: 'blue', 1: 'red'}
            # # ax.scatter(entire_data['principal_feature1'], entire_data['principal_feature2'],
            # #            c=entire_data["anomaly1"].apply(lambda x: colors[x]))
            # # plt.xlabel('principal feature1')
            # # plt.ylabel('principal feature2')
            # # plt.show()
            # print(entire_data.anomaly1.value_counts())
            #
            # fig, ax = plt.subplots(figsize=(15, 10))
            # a = entire_data.loc[entire_data['anomaly1'] == 1, ['aggregate']]  # anomaly
            #
            # ax.plot(entire_data['aggregate'], color='blue', label='正常值')
            # ax.scatter(a['aggregate'].index, a['aggregate'], color='red', label='异常值')
            # plt.xlabel('times', fontsize=25)
            # plt.xticks(fontsize=25)
            # plt.yticks(fontsize=25)
            # plt.ylabel('power', fontsize=25)
            # plt.legend()
            # plt.show()
            # fig.savefig('2.svg', format='svg')  # 输出
            # a.to_csv('异常值.csv')  # 存储到CSV中
            # entire_data.to_csv('entire_data值.csv')
            # entire_data.loc[entire_data['anomaly1'] == 1, ['aggregate']] = np.nan
            # # entire_data.interpolate()
            # entire_data['aggregate'] = entire_data['aggregate'].fillna(method='backfill')
            # entire_data.to_csv('修改entire_data值.csv')
            # return entire_data.values[:, 0], entire_data.values[:, 1:], entire_data.time, tpd
            return entire_data.values[:, 1].astype(np.float64), entire_data.values[:, 2].reshape(-1, 1).astype(
                np.float64), entire_data.time, tpd
