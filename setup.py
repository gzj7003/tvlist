# setup.py
from setuptools import setup, find_packages

setup(
    name="ah-script",
    version="1.0.0",
    author="Your Name",
    description="IPTV playlist generator",
    packages=find_packages(),
    install_requires=[
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.2",
        "opencv-python-headless>=4.8.1",
        "lxml>=4.9.3",
        "numpy>=1.24.3",
        "python-dotenv>=1.0.0",
    ],
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "ahitems=ahitems:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
