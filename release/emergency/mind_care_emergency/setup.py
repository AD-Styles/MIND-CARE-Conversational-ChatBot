from glob import glob
from setuptools import find_packages, setup

package_name = "mind_care_emergency"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test", "tests"]),
    # 부저 알림음 wav — local_buzzer.py 가 패키지 상대경로로 참조.
    # 클린(non-symlink) 빌드에서도 설치되도록 package_data 로 동봉.
    package_data={package_name: ["channels/assets/*.wav"]},
    include_package_data=True,
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
    description="Phase 5 — Emergency Decider + Alert Dispatcher.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "emergency_decider_node = mind_care_emergency.emergency_decider_node:main",
            "alert_dispatcher_node  = mind_care_emergency.alert_dispatcher_node:main",
        ],
    },
)
