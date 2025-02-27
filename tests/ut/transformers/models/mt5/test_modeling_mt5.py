# Copyright 2020 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest

from mindnlp.utils import is_mindspore_available
from mindnlp.utils.testing_utils import require_mindspore, slow


if is_mindspore_available():
    from mindnlp.transformers import AutoModelForSeq2SeqLM, AutoTokenizer


@require_mindspore
class MT5IntegrationTest(unittest.TestCase):
    @slow
    def test_small_integration_test(self):
        """
        For comparision run:
        >>> import t5  # pip install t5==0.7.1
        >>> from t5.data.sentencepiece_vocabulary import SentencePieceVocabulary

        >>> path_to_mtf_small_mt5_checkpoint = '<fill_in>'
        >>> path_to_mtf_small_mt5_spm_model_path = '<fill_in>'
        >>> t5_model = t5.models.MtfModel(model_dir=path_to_mtf_small_mt5_checkpoint, batch_size=1, tpu=None)
        >>> vocab = SentencePieceVocabulary(path_to_mtf_small_mt5_spm_model_path)
        >>> score = t5_model.score(inputs=["Hello there"], targets=["Hi I am"], vocabulary=vocab)
        """

        model = AutoModelForSeq2SeqLM.from_pretrained("google/mt5-small", return_dict=True, from_pt=True)
        tokenizer = AutoTokenizer.from_pretrained("google/mt5-small", from_pt=True)

        input_ids = tokenizer("Hello there", return_tensors="ms").input_ids
        labels = tokenizer("Hi I am", return_tensors="ms").input_ids

        loss = model(input_ids, labels=labels).loss
        mtf_score = -(labels.shape[-1] * loss.item())

        EXPECTED_SCORE = -84.9127
        print(mtf_score)
        self.assertTrue(abs(mtf_score - EXPECTED_SCORE) < 1e-3)
