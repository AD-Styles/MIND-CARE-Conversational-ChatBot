/* nvdsparsebbox_yolov8_pose.cpp
 *
 * ultralytics 의 NMS-baked yolov8n-pose.onnx 출력을 DeepStream nvinfer 의
 * NvDsInferObjectDetectionInfo 리스트로 변환한다.
 *
 * 출력 텐서 형식 (1, 300, 57) — `nms=True` export 기준:
 *   row[ 0..3 ]   x1, y1, x2, y2   (모델 입력 해상도 = 640×640 픽셀)
 *   row[ 4   ]   conf
 *   row[ 5   ]   class id (= 0, person)
 *   row[ 6..56]  17 keypoints × (x, y, visibility)
 *                ↑ DeepStream object meta 에는 bbox + cls 만 저장하고,
 *                  keypoint 는 output-tensor-meta 로 따로 ROS 노드 측 probe
 *                  에서 raw 텐서를 읽어 처리한다.
 *
 * 빌드:
 *   make -C release/vision/mind_care_perception/src/parser_yolov8_pose
 * 산출물:
 *   release/vision/models/pose_estimator/libnvdsinfer_custom_impl_yolov8_pose.so
 */
#include <algorithm>
#include <vector>

#include "nvdsinfer_custom_impl.h"

namespace {

constexpr int OUT_BOXES = 300;
constexpr int OUT_DIM   = 57;   // 6 + 17×3

inline float clampf(float v, float lo, float hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

}  // namespace

extern "C" bool NvDsInferParseYoloPose(
        std::vector<NvDsInferLayerInfo>          const& outputLayersInfo,
        NvDsInferNetworkInfo                     const& networkInfo,
        NvDsInferParseDetectionParams            const& detectionParams,
        std::vector<NvDsInferObjectDetectionInfo>&      objectList);

extern "C" bool NvDsInferParseYoloPose(
        std::vector<NvDsInferLayerInfo>          const& outputLayersInfo,
        NvDsInferNetworkInfo                     const& networkInfo,
        NvDsInferParseDetectionParams            const& detectionParams,
        std::vector<NvDsInferObjectDetectionInfo>&      objectList)
{
    if (outputLayersInfo.empty()) {
        return false;
    }
    const NvDsInferLayerInfo& layer = outputLayersInfo[0];
    if (layer.buffer == nullptr) {
        return false;
    }
    const float* data = static_cast<const float*>(layer.buffer);

    float preThr = 0.0f;
    if (!detectionParams.perClassPreclusterThreshold.empty()) {
        preThr = detectionParams.perClassPreclusterThreshold[0];
    }

    const float netW = static_cast<float>(networkInfo.width);
    const float netH = static_cast<float>(networkInfo.height);

    objectList.reserve(OUT_BOXES);

    for (int i = 0; i < OUT_BOXES; ++i) {
        const float* row = data + i * OUT_DIM;
        const float conf = row[4];
        if (conf <= preThr) {
            continue;
        }

        float x1 = clampf(row[0], 0.0f, netW);
        float y1 = clampf(row[1], 0.0f, netH);
        float x2 = clampf(row[2], 0.0f, netW);
        float y2 = clampf(row[3], 0.0f, netH);
        if (x2 <= x1 || y2 <= y1) {
            continue;
        }

        NvDsInferObjectDetectionInfo obj{};
        obj.classId             = 0;
        obj.detectionConfidence = conf;
        obj.left   = x1;
        obj.top    = y1;
        obj.width  = x2 - x1;
        obj.height = y2 - y1;
        objectList.push_back(obj);
    }

    return true;
}

CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseYoloPose);
