# Custom bbox parser for yolov8n-pose (NMS-baked) → DeepStream nvinfer
#
# 사용:
#   make            # libnvdsinfer_custom_impl_yolov8_pose.so 빌드
#   make clean

CXX     ?= g++
DS_ROOT ?= /opt/nvidia/deepstream/deepstream
DS_INC  := $(DS_ROOT)/sources/includes

ifeq ($(CUDA_INC),)
  CUDA_INC := $(firstword $(wildcard /usr/local/cuda/include /usr/local/cuda-12/include /usr/local/cuda-12.8/include /usr/local/cuda-13/include))
endif

CXXFLAGS := -O2 -fPIC -Wall -Werror -std=c++17 -I$(DS_INC) -I$(CUDA_INC)
LDFLAGS  := -shared

SRC    := nvdsparsebbox_yolov8_pose.cpp
OBJ    := $(SRC:.cpp=.o)
OUT_DIR := $(abspath ../../../models/pose_estimator)
TARGET  := $(OUT_DIR)/libnvdsinfer_custom_impl_yolov8_pose.so

.PHONY: all clean
all: $(TARGET)

$(TARGET): $(OBJ) | $(OUT_DIR)
	$(CXX) $(LDFLAGS) -o $@ $^

%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c -o $@ $<

$(OUT_DIR):
	mkdir -p $@

clean:
	rm -f $(OBJ) $(TARGET)
