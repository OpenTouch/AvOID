#!/usr/bin/env python

from distutils.core import setup
import os


data_files = []

for dirpath, dirnames, filenames in os.walk('avoid-files'):
    data_files.append([dirpath, [os.path.join(dirpath, f) for f in filenames]])

setup(name='AvOID',
      version='0.1',
      description='Ansible Openstack Instance Deployer',
      author='Aymeric BERON',
      author_email='aymeric.beron@alcatel-lucent.com',
      packages=['avoidlib'],
      scripts=['bin/avoid-cli', 'bin/avoid-web', 'bin/avoid-web-cli'],
      data_files = data_files,
     )
