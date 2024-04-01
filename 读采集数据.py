import matplotlib.pyplot as plt
from nptdms import TdmsFile
import numpy as np
import pandas as pd

with TdmsFile.open('采集数据/1211/1211fridge5DD.tdms') as tdms_file:
    for group in tdms_file.groups():      # TdmsFile可以按组名索引来访问TDMS文件中的组，使用groups()方法直接访问所有组
        group_name = group.name
        print(group_name)
    for channel in group.channels():      # TdmsGroup 可以通过通道名称来索引来访问这个组中的一个通道，使用 channels()方法直接访问所有通道
        channel_name = channel.name
        print(channel_name)

    channel = tdms_file['未命名']['未命名']  # 根据索引读取通道
    all_channel_data = channel[:]                  # 将此通道中所有的数据作为numpy数组获取
    num = np.array(all_channel_data)-2082844800
    df = pd.DataFrame(num.astype(int))                         # 将numpy数组中的数据转换成DataFrame并输出
    print(df[:][1:])
    print(df.shape)                                # 维度查看
    channel = np.column_stack((np.array(tdms_file['未命名']['未命名 7']),np.array(tdms_file['未命名']['未命名 8'])))
    all_channel_data = channel[:]                  # 将此通道中所有的数据作为numpy数组获取
    all_channel_data[:, 0] = all_channel_data[:, 0] * 1000 + all_channel_data[:, 1]
    num = np.array(all_channel_data)
    df1 = pd.DataFrame(num)                         # 将numpy数组中的数据转换成DataFrame并输出
    df2 = pd.concat([df, df1], axis=1)
    df2 = df2.iloc[:, 0:2]
    print(df2.shape)                                # 维度查看
    df2.to_csv('导出/1211/MAIN.csv',index=False)