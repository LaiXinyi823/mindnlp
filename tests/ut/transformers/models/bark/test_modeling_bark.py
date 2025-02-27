""" Testing suite for the PyTorch Bark model. """


import copy
import inspect
import tempfile
import unittest

import pytest
import numpy as np

from mindnlp.transformers import (
    BarkCoarseConfig,
    BarkConfig,
    BarkFineConfig,
    BarkSemanticConfig,
)
from mindnlp.transformers.models.bark.generation_bark import (
    BarkCoarseGenerationConfig,
    BarkFineGenerationConfig,
    BarkSemanticGenerationConfig,
)


from mindnlp.transformers.models.bark.process_bark import BarkProcessor
from mindnlp.utils.generic import cached_property
from mindnlp.utils.testing_utils import slow, require_mindspore, is_mindspore_available

from ...generation.test_utils import GenerationTesterMixin
from ...test_configuration_common import ConfigTester
from ...test_modeling_common import ModelTesterMixin, ids_tensor, random_attention_mask
from ..encodec.test_modeling_encodec import EncodecModelTester

if is_mindspore_available():
    import mindspore
    from mindspore import ops
    from mindspore import nn
    from mindnlp.transformers import (
        BarkCausalModel,
        BarkCoarseModel,
        BarkFineModel,
        BarkModel,
        BarkSemanticModel,
    )
mindspore.set_context(pynative_synchronize=True)

class BarkSemanticModelTester:
    def __init__(
        self,
        parent,
        batch_size=2,
        seq_length=4,
        is_training=False,  # for now training is not supported
        use_input_mask=True,
        use_labels=True,
        vocab_size=33,
        output_vocab_size=33,
        hidden_size=16,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=15,
        dropout=0.1,
        window_size=256,
        initializer_range=0.02,
        n_codes_total=8,  # for BarkFineModel
        n_codes_given=1,  # for BarkFineModel
    ):
        self.parent = parent
        self.batch_size = batch_size
        self.seq_length = seq_length
        self.is_training = is_training
        self.use_input_mask = use_input_mask
        self.use_labels = use_labels
        self.vocab_size = vocab_size
        self.output_vocab_size = output_vocab_size
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.intermediate_size = intermediate_size
        self.dropout = dropout
        self.window_size = window_size
        self.initializer_range = initializer_range
        self.bos_token_id = output_vocab_size - 1
        self.eos_token_id = output_vocab_size - 1
        self.pad_token_id = output_vocab_size - 1

        self.n_codes_total = n_codes_total
        self.n_codes_given = n_codes_given

        self.is_encoder_decoder = False

    def prepare_config_and_inputs(self):
        input_ids = ids_tensor([self.batch_size, self.seq_length], self.vocab_size)

        input_mask = None
        if self.use_input_mask:
            input_mask = random_attention_mask([self.batch_size, self.seq_length])

        config = self.get_config()

        head_mask = ids_tensor([self.num_hidden_layers, self.num_attention_heads], 2)

        inputs_dict = {
            "input_ids": input_ids,
            "head_mask": head_mask,
            "attention_mask": input_mask,
        }

        return config, inputs_dict

    def get_config(self):
        return BarkSemanticConfig(
            vocab_size=self.vocab_size,
            output_vocab_size=self.output_vocab_size,
            hidden_size=self.hidden_size,
            num_layers=self.num_hidden_layers,
            num_heads=self.num_attention_heads,
            use_cache=True,
            bos_token_id=self.bos_token_id,
            eos_token_id=self.eos_token_id,
            pad_token_id=self.pad_token_id,
            window_size=self.window_size,
        )

    def get_pipeline_config(self):
        config = self.get_config()
        config.vocab_size = 300
        config.output_vocab_size = 300
        return config

    def prepare_config_and_inputs_for_common(self):
        config, inputs_dict = self.prepare_config_and_inputs()
        return config, inputs_dict

    def create_and_check_decoder_model_past_large_inputs(self, config, inputs_dict):
        model = BarkSemanticModel(config=config).set_train(False)

        input_ids = inputs_dict["input_ids"]
        attention_mask = inputs_dict["attention_mask"]

        # first forward pass
        outputs = model(input_ids, attention_mask=attention_mask, use_cache=True)

        output, past_key_values = outputs.to_tuple()

        # create hypothetical multiple next token and extent to next_input_ids
        next_tokens = ids_tensor((self.batch_size, 3), config.vocab_size)
        next_attn_mask = ids_tensor((self.batch_size, 3), 2)

        # append to next input_ids and
        next_input_ids = ops.cat([input_ids, next_tokens], axis=-1)
        next_attention_mask = ops.cat([attention_mask, next_attn_mask], axis=-1)

        output_from_no_past = model(next_input_ids, attention_mask=next_attention_mask)["logits"]
        output_from_past = model(next_tokens, attention_mask=next_attention_mask, past_key_values=past_key_values)[
            "logits"
        ]

        # select random slice
        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -3:, random_slice_idx].asnumpy()
        output_from_past_slice = output_from_past[:, :, random_slice_idx].asnumpy()

        self.parent.assertTrue(output_from_past_slice.shape[1] == next_tokens.shape[1])

        # test that outputs are equal for slice
        self.parent.assertTrue(np.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))

        # test no attention_mask works
        outputs = model(input_ids, use_cache=True)
        _, past_key_values = outputs.to_tuple()
        output_from_no_past = model(next_input_ids)["logits"]

        output_from_past = model(next_tokens, past_key_values=past_key_values)["logits"]

        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -3:, random_slice_idx].asnumpy()
        output_from_past_slice = output_from_past[:, :, random_slice_idx].asnumpy()
        # test that outputs are equal for slice
        self.parent.assertTrue(np.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))


class BarkCoarseModelTester:
    def __init__(
        self,
        parent,
        batch_size=2,
        seq_length=4,
        is_training=False,  # for now training is not supported
        use_input_mask=True,
        use_labels=True,
        vocab_size=33,
        output_vocab_size=33,
        hidden_size=16,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=15,
        dropout=0.1,
        window_size=256,
        initializer_range=0.02,
        n_codes_total=8,  # for BarkFineModel
        n_codes_given=1,  # for BarkFineModel
    ):
        self.parent = parent
        self.batch_size = batch_size
        self.seq_length = seq_length
        self.is_training = is_training
        self.use_input_mask = use_input_mask
        self.use_labels = use_labels
        self.vocab_size = vocab_size
        self.output_vocab_size = output_vocab_size
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.intermediate_size = intermediate_size
        self.dropout = dropout
        self.window_size = window_size
        self.initializer_range = initializer_range
        self.bos_token_id = output_vocab_size - 1
        self.eos_token_id = output_vocab_size - 1
        self.pad_token_id = output_vocab_size - 1

        self.n_codes_total = n_codes_total
        self.n_codes_given = n_codes_given

        self.is_encoder_decoder = False

    def prepare_config_and_inputs(self):
        input_ids = ids_tensor([self.batch_size, self.seq_length], self.vocab_size)

        input_mask = None
        if self.use_input_mask:
            input_mask = random_attention_mask([self.batch_size, self.seq_length])

        config = self.get_config()

        head_mask = ids_tensor([self.num_hidden_layers, self.num_attention_heads], 2)

        inputs_dict = {
            "input_ids": input_ids,
            "head_mask": head_mask,
            "attention_mask": input_mask,
        }

        return config, inputs_dict

    def get_config(self):
        return BarkCoarseConfig(
            vocab_size=self.vocab_size,
            output_vocab_size=self.output_vocab_size,
            hidden_size=self.hidden_size,
            num_layers=self.num_hidden_layers,
            num_heads=self.num_attention_heads,
            use_cache=True,
            bos_token_id=self.bos_token_id,
            eos_token_id=self.eos_token_id,
            pad_token_id=self.pad_token_id,
            window_size=self.window_size,
        )

    def get_pipeline_config(self):
        config = self.get_config()
        config.vocab_size = 300
        config.output_vocab_size = 300
        return config

    def prepare_config_and_inputs_for_common(self):
        config, inputs_dict = self.prepare_config_and_inputs()
        return config, inputs_dict

    def create_and_check_decoder_model_past_large_inputs(self, config, inputs_dict):
        model = BarkCoarseModel(config=config).set_train(False)

        input_ids = inputs_dict["input_ids"]
        attention_mask = inputs_dict["attention_mask"]

        # first forward pass
        outputs = model(input_ids, attention_mask=attention_mask, use_cache=True)

        output, past_key_values = outputs.to_tuple()

        # create hypothetical multiple next token and extent to next_input_ids
        next_tokens = ids_tensor((self.batch_size, 3), config.vocab_size)
        next_attn_mask = ids_tensor((self.batch_size, 3), 2)

        # append to next input_ids and
        next_input_ids = ops.cat([input_ids, next_tokens], axis=-1)
        next_attention_mask = ops.cat([attention_mask, next_attn_mask], axis=-1)

        output_from_no_past = model(next_input_ids, attention_mask=next_attention_mask)["logits"]
        output_from_past = model(next_tokens, attention_mask=next_attention_mask, past_key_values=past_key_values)[
            "logits"
        ]

        # select random slice
        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -3:, random_slice_idx].asnumpy()
        output_from_past_slice = output_from_past[:, :, random_slice_idx].asnumpy()

        self.parent.assertTrue(output_from_past_slice.shape[1] == next_tokens.shape[1])

        # test that outputs are equal for slice
        self.parent.assertTrue(np.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))

        # test no attention_mask works
        outputs = model(input_ids, use_cache=True)
        _, past_key_values = outputs.to_tuple()
        output_from_no_past = model(next_input_ids)["logits"]

        output_from_past = model(next_tokens, past_key_values=past_key_values)["logits"]

        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -3:, random_slice_idx].asnumpy()
        output_from_past_slice = output_from_past[:, :, random_slice_idx].asnumpy()
        # test that outputs are equal for slice
        self.parent.assertTrue(np.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))


class BarkFineModelTester:
    def __init__(
        self,
        parent,
        batch_size=2,
        seq_length=4,
        is_training=False,  # for now training is not supported
        use_input_mask=True,
        use_labels=True,
        vocab_size=33,
        output_vocab_size=33,
        hidden_size=16,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=15,
        dropout=0.1,
        window_size=256,
        initializer_range=0.02,
        n_codes_total=8,  # for BarkFineModel
        n_codes_given=1,  # for BarkFineModel
    ):
        self.parent = parent
        self.batch_size = batch_size
        self.seq_length = seq_length
        self.is_training = is_training
        self.use_input_mask = use_input_mask
        self.use_labels = use_labels
        self.vocab_size = vocab_size
        self.output_vocab_size = output_vocab_size
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.intermediate_size = intermediate_size
        self.dropout = dropout
        self.window_size = window_size
        self.initializer_range = initializer_range
        self.bos_token_id = output_vocab_size - 1
        self.eos_token_id = output_vocab_size - 1
        self.pad_token_id = output_vocab_size - 1

        self.n_codes_total = n_codes_total
        self.n_codes_given = n_codes_given

        self.is_encoder_decoder = False

    def prepare_config_and_inputs(self):
        input_ids = ids_tensor([self.batch_size, self.seq_length, self.n_codes_total], self.vocab_size)

        input_mask = None
        if self.use_input_mask:
            input_mask = random_attention_mask([self.batch_size, self.seq_length])

        config = self.get_config()

        head_mask = ids_tensor([self.num_hidden_layers, self.num_attention_heads], 2)

        # randint between self.n_codes_given - 1 and self.n_codes_total - 1
        codebook_idx = ids_tensor((1,), self.n_codes_total - self.n_codes_given).item() + self.n_codes_given

        inputs_dict = {
            "codebook_idx": codebook_idx,
            "input_ids": input_ids,
            "head_mask": head_mask,
            "attention_mask": input_mask,
        }

        return config, inputs_dict

    def get_config(self):
        return BarkFineConfig(
            vocab_size=self.vocab_size,
            output_vocab_size=self.output_vocab_size,
            hidden_size=self.hidden_size,
            num_layers=self.num_hidden_layers,
            num_heads=self.num_attention_heads,
            use_cache=True,
            bos_token_id=self.bos_token_id,
            eos_token_id=self.eos_token_id,
            pad_token_id=self.pad_token_id,
            window_size=self.window_size,
        )

    def get_pipeline_config(self):
        config = self.get_config()
        config.vocab_size = 300
        config.output_vocab_size = 300
        return config

    def prepare_config_and_inputs_for_common(self):
        config, inputs_dict = self.prepare_config_and_inputs()
        return config, inputs_dict

    def create_and_check_decoder_model_past_large_inputs(self, config, inputs_dict):
        model = BarkFineModel(config=config).set_train(False)

        input_ids = inputs_dict["input_ids"]
        attention_mask = inputs_dict["attention_mask"]

        # first forward pass
        outputs = model(input_ids, attention_mask=attention_mask, use_cache=True)

        output, past_key_values = outputs.to_tuple()

        # create hypothetical multiple next token and extent to next_input_ids
        next_tokens = ids_tensor((self.batch_size, 3), config.vocab_size)
        next_attn_mask = ids_tensor((self.batch_size, 3), 2)

        # append to next input_ids and
        next_input_ids = ops.cat([input_ids, next_tokens], axis=-1)
        next_attention_mask = ops.cat([attention_mask, next_attn_mask], axis=-1)

        output_from_no_past = model(next_input_ids, attention_mask=next_attention_mask)["logits"]
        output_from_past = model(next_tokens, attention_mask=next_attention_mask, past_key_values=past_key_values)[
            "logits"
        ]

        # select random slice
        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -3:, random_slice_idx].asnumpy()
        output_from_past_slice = output_from_past[:, :, random_slice_idx].asnumpy()

        self.parent.assertTrue(output_from_past_slice.shape[1] == next_tokens.shape[1])

        # test that outputs are equal for slice
        self.parent.assertTrue(np.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))

        # test no attention_mask works
        outputs = model(input_ids, use_cache=True)
        _, past_key_values = outputs.to_tuple()
        output_from_no_past = model(next_input_ids)["logits"]

        output_from_past = model(next_tokens, past_key_values=past_key_values)["logits"]

        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -3:, random_slice_idx].asnumpy()
        output_from_past_slice = output_from_past[:, :, random_slice_idx].asnumpy()
        # test that outputs are equal for slice
        self.parent.assertTrue(np.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))


class BarkModelTester:
    def __init__(
        self,
        parent,
        semantic_kwargs=None,
        coarse_acoustics_kwargs=None,
        fine_acoustics_kwargs=None,
        codec_kwargs=None,
        is_training=False,  # for now training is not supported
    ):
        if semantic_kwargs is None:
            semantic_kwargs = {}
        if coarse_acoustics_kwargs is None:
            coarse_acoustics_kwargs = {}
        if fine_acoustics_kwargs is None:
            fine_acoustics_kwargs = {}
        if codec_kwargs is None:
            codec_kwargs = {}

        self.parent = parent
        self.semantic_model_tester = BarkSemanticModelTester(parent, **semantic_kwargs)
        self.coarse_acoustics_model_tester = BarkCoarseModelTester(parent, **coarse_acoustics_kwargs)
        self.fine_acoustics_model_tester = BarkFineModelTester(parent, **fine_acoustics_kwargs)
        self.codec_model_tester = EncodecModelTester(parent, **codec_kwargs)

        self.is_training = is_training

    def get_config(self):
        return BarkConfig.from_sub_model_configs(
            self.semantic_model_tester.get_config(),
            self.coarse_acoustics_model_tester.get_config(),
            self.fine_acoustics_model_tester.get_config(),
            self.codec_model_tester.get_config(),
        )

    def get_pipeline_config(self):
        config = self.get_config()

        # follow the `get_pipeline_config` of the sub component models
        config.semantic_config.vocab_size = 300
        config.coarse_acoustics_config.vocab_size = 300
        config.fine_acoustics_config.vocab_size = 300

        config.semantic_config.output_vocab_size = 300
        config.coarse_acoustics_config.output_vocab_size = 300
        config.fine_acoustics_config.output_vocab_size = 300

        return config


@require_mindspore
class BarkSemanticModelTest(ModelTesterMixin, GenerationTesterMixin, unittest.TestCase):
    all_model_classes = (BarkSemanticModel,) if is_mindspore_available() else ()
    all_generative_model_classes = (BarkCausalModel,) if is_mindspore_available() else ()

    is_encoder_decoder = False
    fx_compatible = False
    test_missing_keys = False
    test_pruning = False
    test_model_parallel = False
    # no model_parallel for now

    test_resize_embeddings = True

    def setUp(self):
        self.model_tester = BarkSemanticModelTester(self)
        self.config_tester = ConfigTester(self, config_class=BarkSemanticConfig, n_embd=37)

    def test_config(self):
        self.config_tester.run_common_tests()

    # def test_save_load_strict(self):
    #     config, inputs_dict = self.model_tester.prepare_config_and_inputs()
    #     for model_class in self.all_model_classes:
    #         model = model_class(config)

    #         with tempfile.TemporaryDirectory() as tmpdirname:
    #             model.save_pretrained(tmpdirname)
    #             # print(tmpdirname)
    #             model2, info = model_class.from_pretrained(
    #                 tmpdirname, output_loading_info=True)
    #         self.assertEqual(info["missing_keys"], [])

    def test_decoder_model_past_with_large_inputs(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_decoder_model_past_large_inputs(*config_and_inputs)

    def test_inputs_embeds(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            model.set_train(False)

            inputs = copy.deepcopy(self._prepare_for_class(inputs_dict, model_class))

            input_ids = inputs["input_ids"]
            del inputs["input_ids"]

            wte = model.get_input_embeddings()
            inputs["input_embeds"] = wte(input_ids)

            model(**inputs)[0]

    def test_generate_fp16(self):
        config, input_dict = self.model_tester.prepare_config_and_inputs()
        input_ids = input_dict["input_ids"]
        attention_mask = input_ids.ne(1)
        model = self.all_generative_model_classes[0](config).set_train(False)
        model.half()
        model.generate(input_ids, attention_mask=attention_mask)
        model.generate(num_beams=4, do_sample=True, early_stopping=False, num_return_sequences=3)


@require_mindspore
class BarkCoarseModelTest(ModelTesterMixin, GenerationTesterMixin, unittest.TestCase):
    # Same tester as BarkSemanticModelTest, except for model_class and config_class
    all_model_classes = (BarkCoarseModel,) if is_mindspore_available() else ()
    all_generative_model_classes = (BarkCausalModel,) if is_mindspore_available() else ()

    is_encoder_decoder = False
    fx_compatible = False
    test_missing_keys = False
    test_pruning = False
    test_model_parallel = False
    # no model_parallel for now

    test_resize_embeddings = True

    def setUp(self):
        self.model_tester = BarkCoarseModelTester(self)
        self.config_tester = ConfigTester(self, config_class=BarkCoarseConfig, n_embd=37)

    def test_config(self):
        self.config_tester.run_common_tests()

    def test_save_load_strict(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs()
        for model_class in self.all_model_classes:
            model = model_class(config)
            # for name, value in model.parameters_and_names():
            #     print('name:',name)
            #     print('value:',value)
            with tempfile.TemporaryDirectory() as tmpdirname:
                model.save_pretrained(tmpdirname)
                model2, info = model_class.from_pretrained(tmpdirname, output_loading_info=True)
            # for name, value in model2.parameters_and_names():
            #     print('key:',name)
            #     print('value:',value.shape)
            # print('miss_key:',info['missing_keys'])
            self.assertEqual(info["missing_keys"], [])

    def test_decoder_model_past_with_large_inputs(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_decoder_model_past_large_inputs(*config_and_inputs)

    def test_inputs_embeds(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            model.set_train(False)

            inputs = copy.deepcopy(self._prepare_for_class(inputs_dict, model_class))

            input_ids = inputs["input_ids"]
            del inputs["input_ids"]

            wte = model.get_input_embeddings()
            inputs["input_embeds"] = wte(input_ids)

            model(**inputs)[0]

    def test_generate_fp16(self):
        config, input_dict = self.model_tester.prepare_config_and_inputs()
        input_ids = input_dict["input_ids"]
        attention_mask = input_ids.ne(1)
        model = self.all_generative_model_classes[0](config).set_train(False)
        model.half()
        model.generate(input_ids, attention_mask=attention_mask)
        model.generate(num_beams=4, do_sample=True, early_stopping=False, num_return_sequences=3)


@require_mindspore
class BarkFineModelTest(ModelTesterMixin, unittest.TestCase):
    all_model_classes = (BarkFineModel,) if is_mindspore_available() else ()

    is_encoder_decoder = False
    fx_compatible = False
    test_missing_keys = False
    test_pruning = False
    # no model_parallel for now
    test_model_parallel = False

    # torchscript disabled for now because forward with an int
    test_torchscript = False

    test_resize_embeddings = True

    def setUp(self):
        self.model_tester = BarkFineModelTester(self)
        self.config_tester = ConfigTester(self, config_class=BarkFineConfig, n_embd=37)

    def test_config(self):
        self.config_tester.run_common_tests()

    def test_save_load_strict(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs()
        for model_class in self.all_model_classes:
            model = model_class(config)

            with tempfile.TemporaryDirectory() as tmpdirname:
                # print(model)
                model.save_pretrained(tmpdirname)
                model2, info = model_class.from_pretrained(tmpdirname, output_loading_info=True)
            self.assertEqual(info["missing_keys"], [])

    def test_inputs_embeds(self):
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            model.set_train(False)

            inputs = copy.deepcopy(self._prepare_for_class(inputs_dict, model_class))

            input_ids = inputs["input_ids"]
            del inputs["input_ids"]

            wte = model.get_input_embeddings()[inputs_dict["codebook_idx"]]

            inputs["input_embeds"] = wte(input_ids[:, :, inputs_dict["codebook_idx"]])


            model(**inputs)[0]

    def test_generate_fp16(self):
        config, input_dict = self.model_tester.prepare_config_and_inputs()
        input_ids = input_dict["input_ids"]
        # take first codebook channel

        model = self.all_model_classes[0](config).set_train(False)
        model.half()

        # toy generation_configs
        semantic_generation_config = BarkSemanticGenerationConfig(semantic_vocab_size=0)
        coarse_generation_config = BarkCoarseGenerationConfig(n_coarse_codebooks=config.n_codes_given)
        fine_generation_config = BarkFineGenerationConfig(
            max_fine_history_length=config.block_size // 2,
            max_fine_input_length=config.block_size,
            n_fine_codebooks=config.n_codes_total,
        )
        codebook_size = config.vocab_size - 1

        model.generate(
            input_ids,
            history_prompt=None,
            temperature=None,
            semantic_generation_config=semantic_generation_config,
            coarse_generation_config=coarse_generation_config,
            fine_generation_config=fine_generation_config,
            codebook_size=codebook_size,
        )

        model.generate(
            input_ids,
            history_prompt=None,
            temperature=0.7,
            semantic_generation_config=semantic_generation_config,
            coarse_generation_config=coarse_generation_config,
            fine_generation_config=fine_generation_config,
            codebook_size=codebook_size,
        )

    def test_forward_signature(self):
        config, _ = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            signature = inspect.signature(model.construct)
            # signature.parameters is an OrderedDict => so arg_names order is deterministic
            arg_names = [*signature.parameters.keys()]

            expected_arg_names = ["codebook_idx", "input_ids"]
            self.assertListEqual(arg_names[:2], expected_arg_names)

    def test_model_common_attributes(self):
        # one embedding layer per codebook
        config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()

        for model_class in self.all_model_classes:
            model = model_class(config)
            self.assertIsInstance(model.get_input_embeddings()[0], (nn.Embedding))
            model.set_input_embeddings(
                nn.CellList([nn.Embedding(10, 10) for _ in range(config.n_codes_total)])
            )
            x = model.get_output_embeddings()
            self.assertTrue(x is None or isinstance(x[0], nn.Dense))

    def test_resize_tokens_embeddings(self):
        # resizing tokens_embeddings of a ModuleList
        original_config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
        if not self.test_resize_embeddings:
            return

        for model_class in self.all_model_classes:
            config = copy.deepcopy(original_config)
            model = model_class(config)

            if self.model_tester.is_training is False:
                model.set_train(False)

            model_vocab_size = config.vocab_size
            # Retrieve the embeddings and clone theme
            model_embed_list = model.resize_token_embeddings(model_vocab_size)
            cloned_embeddings_list = [model_embed.weight.clone() for model_embed in model_embed_list]

            # Check that resizing the token embeddings with a larger vocab size increases the model's vocab size
            model_embed_list = model.resize_token_embeddings(model_vocab_size + 10)
            self.assertEqual(model.config.vocab_size, model_vocab_size + 10)

            # Check that it actually resizes the embeddings matrix for each codebook
            for model_embed, cloned_embeddings in zip(model_embed_list, cloned_embeddings_list):
                self.assertEqual(model_embed.weight.shape[0], cloned_embeddings.shape[0] + 10)

            # Check that the model can still do a forward pass successfully (every parameter should be resized)
            model(**self._prepare_for_class(inputs_dict, model_class))

            # Check that resizing the token embeddings with a smaller vocab size decreases the model's vocab size
            model_embed_list = model.resize_token_embeddings(model_vocab_size - 15)
            self.assertEqual(model.config.vocab_size, model_vocab_size - 15)
            for model_embed, cloned_embeddings in zip(model_embed_list, cloned_embeddings_list):
                self.assertEqual(model_embed.weight.shape[0], cloned_embeddings.shape[0] - 15)

            # Check that the model can still do a forward pass successfully (every parameter should be resized)
            # Input ids should be clamped to the maximum size of the vocabulary
            print(inputs_dict['input_ids'])
            inputs_dict["input_ids"] = ops.clamp(inputs_dict["input_ids"],min=0,max =model_vocab_size-16)
            # inputs_dict["input_ids"].clamp(max=model_vocab_size - 15 - 1)
            print(inputs_dict['input_ids'])
            model(**self._prepare_for_class(inputs_dict, model_class))

            # Check that adding and removing tokens has not modified the first part of the embedding matrix.
            # only check for the first embedding matrix
            models_equal = True
            for p1, p2 in zip(cloned_embeddings_list[0], model_embed_list[0].weight):
                if ops.ne(p1,p2).sum() > 0:
                    models_equal = False

            self.assertTrue(models_equal)

    def test_resize_embeddings_untied(self):
        # resizing tokens_embeddings of a ModuleList
        original_config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
        if not self.test_resize_embeddings:
            return

        original_config.tie_word_embeddings = False

        for model_class in self.all_model_classes:
            config = copy.deepcopy(original_config)
            model = model_class(config)

            # if no output embeddings -> leave test
            if model.get_output_embeddings() is None:
                continue

            # Check that resizing the token embeddings with a larger vocab size increases the model's vocab size
            model_vocab_size = config.vocab_size
            model.resize_token_embeddings(model_vocab_size + 10)
            self.assertEqual(model.config.vocab_size, model_vocab_size + 10)
            output_embeds_list = model.get_output_embeddings()

            for output_embeds in output_embeds_list:
                self.assertEqual(output_embeds.weight.shape[0], model_vocab_size + 10)

                # Check bias if present
                if output_embeds.bias is not None:
                    self.assertEqual(output_embeds.bias.shape[0], model_vocab_size + 10)

            # Check that the model can still do a forward pass successfully (every parameter should be resized)
            model(**self._prepare_for_class(inputs_dict, model_class))

            # Check that resizing the token embeddings with a smaller vocab size decreases the model's vocab size
            model.resize_token_embeddings(model_vocab_size - 15)
            self.assertEqual(model.config.vocab_size, model_vocab_size - 15)
            # Check that it actually resizes the embeddings matrix
            output_embeds_list = model.get_output_embeddings()

            for output_embeds in output_embeds_list:
                self.assertEqual(output_embeds.weight.shape[0], model_vocab_size - 15)
                # Check bias if present
                if output_embeds.bias is not None:
                    self.assertEqual(output_embeds.bias.shape[0], model_vocab_size - 15)

            # Check that the model can still do a forward pass successfully (every parameter should be resized)
            # Input ids should be clamped to the maximum size of the vocabulary
            inputs_dict["input_ids"] = ops.clamp(inputs_dict["input_ids"],min=0,max =model_vocab_size-16)
            # inputs_dict["input_ids"].clamp(max=model_vocab_size - 15 - 1)

            # Check that the model can still do a forward pass successfully (every parameter should be resized)
            model(**self._prepare_for_class(inputs_dict, model_class))

    @pytest.mark.flash_attn_test
    @slow
    def test_flash_attn_2_inference(self):
        for model_class in self.all_model_classes:
            if not model_class._supports_flash_attn_2:
                return

            config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
            model = model_class(config)

            with tempfile.TemporaryDirectory() as tmpdirname:
                model.save_pretrained(tmpdirname)
                model_fa = model_class.from_pretrained(
                    tmpdirname, torch_dtype=mindspore.bfloat16, use_flash_attention_2=True
                )

                model = model_class.from_pretrained(
                    tmpdirname, torch_dtype=mindspore.bfloat16, use_flash_attention_2=False
                )

                dummy_input = inputs_dict["input_ids"][:1]
                if dummy_input.dtype in [mindspore.float32, mindspore.float16]:
                    dummy_input = dummy_input.to(mindspore.bfloat16)

                dummy_attention_mask = inputs_dict.get("attention_mask", None)

                if dummy_attention_mask is not None:
                    dummy_attention_mask = dummy_attention_mask[:1]
                    dummy_attention_mask[:, 1:] = 1
                    dummy_attention_mask[:, :1] = 0

                outputs = model(inputs_dict["codebook_idx"], dummy_input, output_hidden_states=True)
                outputs_fa = model_fa(inputs_dict["codebook_idx"], dummy_input, output_hidden_states=True)

                logits = outputs.hidden_states[-1]
                logits_fa = outputs_fa.hidden_states[-1]

                assert np.allclose(logits_fa, logits, atol=4e-2, rtol=4e-2)

                other_inputs = {"output_hidden_states": True}
                if dummy_attention_mask is not None:
                    other_inputs["attention_mask"] = dummy_attention_mask

                outputs = model(inputs_dict["codebook_idx"], dummy_input, **other_inputs)
                outputs_fa = model_fa(inputs_dict["codebook_idx"], dummy_input, **other_inputs)

                logits = outputs.hidden_states[-1]
                logits_fa = outputs_fa.hidden_states[-1]

                assert np.allclose(logits_fa[1:], logits[1:], atol=4e-2, rtol=4e-2)

                # check with inference + dropout
                model.train()
                _ = model_fa(inputs_dict["codebook_idx"], dummy_input, **other_inputs)

    @pytest.mark.flash_attn_test
    @slow
    def test_flash_attn_2_inference_padding_right(self):
        for model_class in self.all_model_classes:
            if not model_class._supports_flash_attn_2:
                return

            config, inputs_dict = self.model_tester.prepare_config_and_inputs_for_common()
            model = model_class(config)

            with tempfile.TemporaryDirectory() as tmpdirname:
                model.save_pretrained(tmpdirname)
                model_fa = model_class.from_pretrained(
                    tmpdirname, torch_dtype=mindspore.bfloat16, use_flash_attention_2=True
                )


                model = model_class.from_pretrained(
                    tmpdirname, torch_dtype=mindspore.bfloat16, use_flash_attention_2=False
                )


                dummy_input = inputs_dict["input_ids"][:1]
                if dummy_input.dtype in [mindspore.float32, mindspore.float16]:
                    dummy_input = dummy_input.to(mindspore.bfloat16)

                dummy_attention_mask = inputs_dict.get("attention_mask", None)

                if dummy_attention_mask is not None:
                    dummy_attention_mask = dummy_attention_mask[:1]
                    dummy_attention_mask[:, :-1] = 1
                    dummy_attention_mask[:, -1:] = 0

                outputs = model(inputs_dict["codebook_idx"], dummy_input, output_hidden_states=True)
                outputs_fa = model_fa(inputs_dict["codebook_idx"], dummy_input, output_hidden_states=True)

                logits = outputs.hidden_states[-1]
                logits_fa = outputs_fa.hidden_states[-1]

                assert np.allclose(logits_fa, logits, atol=4e-2, rtol=4e-2)

                other_inputs = {
                    "output_hidden_states": True,
                }
                if dummy_attention_mask is not None:
                    other_inputs["attention_mask"] = dummy_attention_mask

                outputs = model(inputs_dict["codebook_idx"], dummy_input, **other_inputs)
                outputs_fa = model_fa(inputs_dict["codebook_idx"], dummy_input, **other_inputs)

                logits = outputs.hidden_states[-1]
                logits_fa = outputs_fa.hidden_states[-1]

                assert np.allclose(logits_fa[:-1], logits[:-1], atol=4e-2, rtol=4e-2)


@require_mindspore
class BarkModelIntegrationTests(unittest.TestCase):
    r"""
    Test BarkModelIntegration
    """
    @cached_property
    def model(self):
        return BarkModel.from_pretrained("suno/bark")

    @cached_property
    def processor(self):
        return BarkProcessor.from_pretrained("suno/bark")

    @cached_property
    def inputs(self):
        input_ids = self.processor("In the light of the moon, a little egg lay on a leaf", voice_preset="en_speaker_6")

        input_ids = input_ids

        return input_ids

    @cached_property
    def semantic_generation_config(self):
        semantic_generation_config = BarkSemanticGenerationConfig(**self.model.generation_config.semantic_config)
        return semantic_generation_config

    @cached_property
    def coarse_generation_config(self):
        coarse_generation_config = BarkCoarseGenerationConfig(**self.model.generation_config.coarse_acoustics_config)
        return coarse_generation_config

    @cached_property
    def fine_generation_config(self):
        fine_generation_config = BarkFineGenerationConfig(**self.model.generation_config.fine_acoustics_config)
        return fine_generation_config

    @slow
    def test_generate_semantic(self):
        input_ids = self.inputs

        # check first ids
        expected_output_ids = [7363, 321, 41, 1461, 6915, 952, 326, 41, 41, 927,]  # fmt: skip

        # greedy decoding
        output_ids = self.model.semantic.generate(
            **input_ids,
            do_sample=False,
            temperature=1.0,
            semantic_generation_config=self.semantic_generation_config,
        )
        self.assertListEqual(output_ids[0, : len(expected_output_ids)].tolist(), expected_output_ids)

    @slow
    def test_generate_semantic_early_stop(self):
        input_ids = self.inputs
        min_eos_p = 0.01

        # check first ids
        expected_output_ids = [7363, 321, 41, 1461, 6915, 952, 326, 41, 41, 927,]  # fmt: skip

        # Should be able to read min_eos_p from kwargs

        # mindspore.manual_seed(0)
        output_ids_without_min_eos_p = self.model.semantic.generate(
            **input_ids,
            do_sample=False,
            temperature=0.9,
            semantic_generation_config=self.semantic_generation_config,
        )
        # mindspore.manual_seed(0)
        output_ids_kwargs = self.model.semantic.generate(
            **input_ids,
            do_sample=False,
            temperature=0.9,
            semantic_generation_config=self.semantic_generation_config,
            min_eos_p=min_eos_p,
        )
        self.assertListEqual(output_ids_without_min_eos_p[0, : len(expected_output_ids)].tolist(), expected_output_ids)
        self.assertLess(len(output_ids_kwargs[0, :].tolist()), len(output_ids_without_min_eos_p[0, :].tolist()))

        # Should be able to read min_eos_p from the semantic generation config
        self.semantic_generation_config.min_eos_p = min_eos_p

        # torch.manual_seed(0)
        output_ids = self.model.semantic.generate(
            **input_ids,
            do_sample=False,
            temperature=0.9,
            semantic_generation_config=self.semantic_generation_config,
        )

        self.assertEqual(output_ids.shape, output_ids_kwargs.shape)
        self.assertLess(len(output_ids[0, :].tolist()), len(output_ids_without_min_eos_p[0, :].tolist()))
        self.assertListEqual(output_ids[0, : len(expected_output_ids)].tolist(), expected_output_ids)

    @slow
    def test_generate_coarse(self):
        input_ids = self.inputs

        history_prompt = input_ids["history_prompt"]

        # check first ids
        expected_output_ids = [11018, 11391, 10651, 11418, 10857, 11620, 10642, 11366, 10312, 11528, 10531, 11516, 10474, 11051, 10524, 11051, ]  # fmt: skip


        output_ids = self.model.semantic.generate(
            **input_ids,
            do_sample=False,
            temperature=1.0,
            semantic_generation_config=self.semantic_generation_config,
        )

        output_ids = self.model.coarse_acoustics.generate(
            output_ids,
            history_prompt=history_prompt,
            do_sample=False,
            temperature=1.0,
            semantic_generation_config=self.semantic_generation_config,
            coarse_generation_config=self.coarse_generation_config,
            codebook_size=self.model.generation_config.codebook_size,
        )

        self.assertListEqual(output_ids[0, : len(expected_output_ids)].tolist(), expected_output_ids)

    @slow
    def test_generate_fine(self):
        input_ids = self.inputs

        history_prompt = input_ids["history_prompt"]

        # fmt: off
        expected_output_ids = [
            [1018, 651, 857, 642, 312, 531, 474, 524, 524, 776,],
            [367, 394, 596, 342, 504, 492, 27, 27, 822, 822,],
            [961, 955, 221, 955, 955, 686, 939, 939, 479, 176,],
            [638, 365, 218, 944, 853, 363, 639, 22, 884, 456,],
            [302, 912, 524, 38, 174, 209, 879, 23, 910, 227,],
            [440, 673, 861, 666, 372, 558, 49, 172, 232, 342,],
            [244, 358, 123, 356, 586, 520, 499, 877, 542, 637,],
            [806, 685, 905, 848, 803, 810, 921, 208, 625, 203,],
        ]
        # fmt: on


        output_ids = self.model.semantic.generate(
            **input_ids,
            do_sample=False,
            temperature=1.0,
            semantic_generation_config=self.semantic_generation_config,
        )

        output_ids = self.model.coarse_acoustics.generate(
            output_ids,
            history_prompt=history_prompt,
            do_sample=False,
            temperature=1.0,
            semantic_generation_config=self.semantic_generation_config,
            coarse_generation_config=self.coarse_generation_config,
            codebook_size=self.model.generation_config.codebook_size,
        )

        # greedy decoding
        output_ids = self.model.fine_acoustics.generate(
            output_ids,
            history_prompt=history_prompt,
            temperature=None,
            semantic_generation_config=self.semantic_generation_config,
            coarse_generation_config=self.coarse_generation_config,
            fine_generation_config=self.fine_generation_config,
            codebook_size=self.model.generation_config.codebook_size,
        )

        self.assertListEqual(output_ids[0, :, : len(expected_output_ids[0])].tolist(), expected_output_ids)

    @slow
    def test_generate_end_to_end(self):
        input_ids = self.inputs

        self.model.generate(**input_ids)
        self.model.generate(**{key: val for (key, val) in input_ids.items() if key != "history_prompt"})

    @slow
    def test_generate_end_to_end_with_args(self):
        input_ids = self.inputs


        self.model.generate(**input_ids, do_sample=True, temperature=0.6, penalty_alpha=0.6)
        self.model.generate(**input_ids, do_sample=True, temperature=0.6, num_beams=4)

    @slow
    def test_generate_batching(self):
        args = {"do_sample": False, "temperature": None}

        s1 = "I love HuggingFace"
        s2 = "In the light of the moon, a little egg lay on a leaf"
        voice_preset = "en_speaker_6"
        input_ids = self.processor([s1, s2], voice_preset=voice_preset)

        # generate in batch
        outputs, audio_lengths = self.model.generate(**input_ids, **args, return_output_lengths=True)

        # generate one-by-one
        s1 = self.processor(s1, voice_preset=voice_preset)
        s2 = self.processor(s2, voice_preset=voice_preset)
        output1 = self.model.generate(**s1, **args)
        output2 = self.model.generate(**s2, **args)

        # up until the coarse acoustic model (included), results are the same
        # the fine acoustic model introduces small differences
        # first verify if same length (should be the same because it's decided in the coarse model)
        self.assertEqual(tuple(audio_lengths), (output1.shape[1], output2.shape[1]))

        # then assert almost equal
        self.assertTrue(np.allclose(outputs[0, : audio_lengths[0]], output1.squeeze(), atol=2e-3))
        self.assertTrue(np.allclose(outputs[1, : audio_lengths[1]], output2.squeeze(), atol=2e-3))

        # now test single input with return_output_lengths = True
        outputs, _ = self.model.generate(**s1, **args, return_output_lengths=True)
        self.assertTrue((outputs == output1).all().item())

    @slow
    def test_generate_end_to_end_with_sub_models_args(self):
        input_ids = self.inputs

        # torch.manual_seed(0)
        self.model.generate(
            **input_ids, do_sample=False, temperature=1.0, coarse_do_sample=True, coarse_temperature=0.7
        )
        output_ids_without_min_eos_p = self.model.generate(
            **input_ids,
            do_sample=True,
            temperature=0.9,
            coarse_do_sample=True,
            coarse_temperature=0.7,
            fine_temperature=0.3,
        )

        output_ids_with_min_eos_p = self.model.generate(
            **input_ids,
            do_sample=True,
            temperature=0.9,
            coarse_temperature=0.7,
            fine_temperature=0.3,
            min_eos_p=0.1,
        )
        self.assertLess(
            len(output_ids_with_min_eos_p[0, :].tolist()), len(output_ids_without_min_eos_p[0, :].tolist())
        )
