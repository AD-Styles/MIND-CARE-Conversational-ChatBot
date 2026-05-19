from glob import glob

from setuptools import find_packages, setup

package_name = "mind_care_perception"

setup(
    name=package_name,
    version="0.2.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
            ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="포테토 팀",
    maintainer_email="team-potato@example.com",
    description=(
        "마음돌봄 Vision 인지 노드 — Phase 1: dry-run 에뮬레이터 / "
        "Phase 2: DeepStream 8.0 (YOLOv8n-face + Emotion SGIE)"
    ),
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "vision_emulator_node = mind_care_perception.vision_emulator_node:main",
            "vision_deepstream_node = mind_care_perception.vision_deepstream_node:main",
            "fall_detection_node = mind_care_perception.fall_detection_node:main",
        ],
    },
)
