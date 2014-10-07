#!/usr/bin/env python
# Copyright (c) 2014 Alcatel-Lucent Enterprise
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

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
