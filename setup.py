from __future__ import annotations

import pathlib
import re

from setuptools import find_packages, setup

ROOT = pathlib.Path(__file__).parent

readme_path = ROOT / "README.md"
README = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""

version_path = ROOT / "concordia" / "__init__.py"
version_text = version_path.read_text(encoding="utf-8")
match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', version_text, re.M)
if not match:
    raise RuntimeError("Unable to find __version__ in concordia/__init__.py")
VERSION = match.group(1)

setup(
    name="concordia",
    version=VERSION,
    description="Multi-user shared terminal",
    long_description=README,
    long_description_content_type="text/markdown",
    author="Concordia Contributors",
    author_email="atharva.jgupta@gmail.com",
    license="Proprietary",
    packages=find_packages(exclude=("tests*", "docs*")),
    include_package_data=True,
    package_data={"concordia.ui": ["*.tcss"]},
    python_requires=">=3.9",
    install_requires=[
        "websockets>=12.0",
        "python-dotenv>=1.0.0",
        "requests>=2.31.0",
        "pyngrok>=7.0.0",
        "prompt_toolkit>=3.0.0",
    ],
    entry_points={
        "console_scripts": [
            "concordia=concordia.cli:main",
            "concordia_host=concordia.host_cli:main",
            "concordia_client=concordia.client_cli:main",
        ]
    },
)
