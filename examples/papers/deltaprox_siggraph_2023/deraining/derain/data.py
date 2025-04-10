import os
from .dataset import DataLoaderTrain, DataLoaderVal, DataLoaderTest, DataLoaderTest_fine

def get_training_data(rgb_dir, img_options):
    assert os.path.exists(rgb_dir)
    return DataLoaderTrain(rgb_dir, img_options)

def get_validation_data(rgb_dir, img_options):
    assert os.path.exists(rgb_dir)
    return DataLoaderVal(rgb_dir, img_options)

def get_test_data(rgb_dir, img_options):
    assert os.path.exists(rgb_dir)
    return DataLoaderTest(rgb_dir, img_options)

def get_test_data_fine(rgb_dir, img_options):
    assert os.path.exists(rgb_dir)
    return DataLoaderTest_fine(rgb_dir, img_options)