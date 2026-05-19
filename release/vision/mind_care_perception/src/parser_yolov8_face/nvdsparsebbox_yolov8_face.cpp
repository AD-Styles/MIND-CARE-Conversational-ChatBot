/* nvdsparsebbox_yolov8_face.cpp
 *
 * akanametov/yolo-face 의 NMS-baked yolov8n-face.onnx 출력을
 * DeepStream nvinfer 의 NvDsInferObjectDetectionInfo 리스트로 변환한다.
 *
 * 출력 텐서 형식 (1, 300, 21):
 *   row[0..3]   x1, y1, x2, y2  (모델 입력 해상도, 즉 640×640 픽셀 좌표계)
 *   row[4]      face confidence
 *   row[5]      class id (= 0)
 *   row[6..20]  5 keypoints × (x, y, visibility)   ← face landmark, 본 파서에서는 무시
 *
 * NMS 가 ONNX 그래프 안에 이미 포함되어 있으므로,
 * pgie config 의 cluster-mode 는 4(no-cluster) 로 두어도 충분하다.
 *
 * 빌드:
 *   make -C release/vision/mind_care_perception/src/parser_yolov8_face
 * 산출물:
 *   release/vision/models/face_detector/libnvdsinfer_custom_impl_yolov8_face.so
 */
#include <algorithm>
#include <cstring>
#include <vector>

#include "nvdsinfer_custom_impl.h"

namespace {

constexpr int OUT_BOXES   = 300;   // ONNX baked-NMS 가 만드는 고정 슬롯 수
constexpr int OUT_DIM     = 21;    // 위 주석 참조

inline float clampf(float v, float lo, float hi) {
    return v < lo ? lo : (v > hi ? hi : v);
}

}  // namespace

extern "C" bool NvDsInferParseYoloFace(
        std::vector<NvDsInferLayerInfo>          const& outputLayersInfo,
        NvDsInferNetworkInfo                     const& networkInfo,
        NvDsInferParseDetectionParams            const& detectionParams,
        std::vector<NvDsInferObjectDetectionInfo>&      objectList);

extern "C" bool NvDsInferParseYoloFace(
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

    // pre-cluster threshold 는 class 0 (face) 에 적용
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

        // NMS-baked 출력이라 빈 슬롯은 0 으로 채워져 있음
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

CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseYoloFace);
