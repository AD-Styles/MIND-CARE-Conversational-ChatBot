from glob import glob
from setuptools import find_packages, setup

package_name = "mind_care_api"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test", "tests"]),
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
    description="Phase 5 — FastAPI gateway for mobile app.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "api_gateway_node = mind_care_api.api_gateway_node:main",
        ],
    },
)
