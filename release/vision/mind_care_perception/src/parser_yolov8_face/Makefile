# Custom bbox parser for akanametov/yolov8n-face (NMS-baked) → DeepStream nvinfer
#
# 사용:
#   make            # libnvdsinfer_custom_impl_yolov8_face.so 빌드
#   make clean

CXX     ?= g++
DS_ROOT ?= /opt/nvidia/deepstream/deepstream
DS_INC  := $(DS_ROOT)/sources/includes

# CUDA include — 시스템에 여러 버전이 있을 수 있어 자동 탐색.
# 환경변수 CUDA_INC 로 강제 가능.
ifeq ($(CUDA_INC),)
  CUDA_INC := $(firstword $(wildcard /usr/local/cuda/include /usr/local/cuda-12/include /usr/local/cuda-12.8/include /usr/local/cuda-13/include))
endif

CXXFLAGS := -O2 -fPIC -Wall -Werror -std=c++17 -I$(DS_INC) -I$(CUDA_INC)
LDFLAGS  := -shared

SRC    := nvdsparsebbox_yolov8_face.cpp
OBJ    := $(SRC:.cpp=.o)

# 산출물은 모델 디렉터리에 직접 — pgie config 의 custom-lib-path 와 일치
OUT_DIR := $(abspath ../../../models/face_detector)
TARGET  := $(OUT_DIR)/libnvdsinfer_custom_impl_yolov8_face.so

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
