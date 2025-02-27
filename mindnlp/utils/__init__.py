# Copyright 2021 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================

"""
Common utils
"""
from .generic import *
from .decompress import unzip, untar, ungz
from .download import *
from .compatibility import *
from .import_utils import requires_backends, is_mindspore_available, OptionalDependencyNotAvailable, is_sentencepiece_available, \
is_tokenizers_available, direct_transformers_import, is_protobuf_available, is_safetensors_available, \
is_cython_available, is_pretty_midi_available, is_essentia_available, is_librosa_available, is_scipy_available

from .testing_utils import require_mindspore
from .save import convert_file_size_to_int

DUMMY_INPUTS = [[7, 6, 0, 0, 1], [1, 2, 3, 0, 0], [0, 0, 0, 4, 5]]
DUMMY_MASK = [[1, 1, 1, 1, 1], [1, 1, 1, 0, 0], [0, 0, 0, 1, 1]]
