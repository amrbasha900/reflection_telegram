# -*- coding: utf-8 -*-
from setuptools import setup, find_packages

with open('requirements.txt') as f:
	install_requires = f.read().strip().split('\n')

# get version from __version__ variable in reflection_telegram/__init__.py
from reflection_telegram import __version__ as version

setup(
	name='reflection_telegram',
	version=version,
	description='Telegram integration for Frappe/ERPNext: QR onboarding, rate-limited bulk sending, and a reusable API',
	author='Amr Basha',
	author_email='amrbasha900@users.noreply.github.com',
	url='https://github.com/amrbasha900/reflection_telegram',
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
