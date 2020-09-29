#!/usr/bin/env python3
# coding=utf-8
# Copyright 2018 Google AI, Google Brain and Carnegie Mellon University Authors and the HuggingFace Inc. team.
# Copyright (c) 2018, NVIDIA CORPORATION.  All rights reserved.
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
""" Conditional text generation with the auto-regressive models of the library (GPT/GPT-2/CTRL/Transformer-XL/XLNet)
"""


import argparse
import logging
from tqdm import tqdm
import numpy as np
import torch
import pandas as pd
from transformers import (
    CTRLLMHeadModel,
    CTRLTokenizer,
    GPT2Tokenizer,
    OpenAIGPTLMHeadModel,
    OpenAIGPTTokenizer,
    TransfoXLLMHeadModel,
    TransfoXLTokenizer,
    XLMTokenizer,
    XLMWithLMHeadModel,
    XLNetLMHeadModel,
    XLNetTokenizer,
    GPT2LMHeadModel
)
# from Module.GPT2Model import GPT2LMHeadModel

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s -   %(message)s", datefmt="%m/%d/%Y %H:%M:%S", level=logging.INFO,
)
logger = logging.getLogger(__name__)

MAX_LENGTH = int(10000)  # Hardcoded max length to avoid infinite loop

MODEL_CLASSES = {
    "gpt2": (GPT2LMHeadModel, GPT2Tokenizer),
    "distilgpt2": (GPT2LMHeadModel, GPT2Tokenizer),
    "ctrl": (CTRLLMHeadModel, CTRLTokenizer),
    "openai-gpt": (OpenAIGPTLMHeadModel, OpenAIGPTTokenizer),
    "xlnet": (XLNetLMHeadModel, XLNetTokenizer),
    "transfo-xl": (TransfoXLLMHeadModel, TransfoXLTokenizer),
    "xlm": (XLMWithLMHeadModel, XLMTokenizer),
}

# Padding text to help Transformer-XL and XLNet with short prompts as proposed by Aman Rusia
# in https://github.com/rusiaaman/XLNet-gen#methodology
# and https://medium.com/@amanrusia/xlnet-speaks-comparison-to-gpt-2-ea1a4e9ba39e
PADDING_TEXT = """ In 1991, the remains of Russian Tsar Nicholas II and his family
(except for Alexei and Maria) are discovered.
The voice of Nicholas's young son, Tsarevich Alexei Nikolaevich, narrates the
remainder of the story. 1883 Western Siberia,
a young Grigori Rasputin is asked by his father and a group of men to perform magic.
Rasputin has a vision and denounces one of the men as a horse thief. Although his
father initially slaps him for making such an accusation, Rasputin watches as the
man is chased outside and beaten. Twenty years later, Rasputin sees a vision of
the Virgin Mary, prompting him to become a priest. Rasputin quickly becomes famous,
with people, even a bishop, begging for his blessing. <eod> </s> <eos>"""


def set_seed(args):
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)


#
# Functions to prepare models' input
#


def prepare_ctrl_input(args, _, tokenizer, prompt_text):
    if args.temperature > 0.7:
        logger.info("CTRL typically works better with lower temperatures (and lower top_k).")

    encoded_prompt = tokenizer.encode(prompt_text, add_special_tokens=False)
    if not any(encoded_prompt[0] == x for x in tokenizer.control_codes.values()):
        logger.info("WARNING! You are not starting your generation from a control code so you won't get good results")
    return prompt_text


def prepare_xlm_input(args, model, tokenizer, prompt_text):
    # kwargs = {"language": None, "mask_token_id": None}

    # Set the language
    use_lang_emb = hasattr(model.config, "use_lang_emb") and model.config.use_lang_emb
    if hasattr(model.config, "lang2id") and use_lang_emb:
        available_languages = model.config.lang2id.keys()
        if args.xlm_language in available_languages:
            language = args.xlm_language
        else:
            language = None
            while language not in available_languages:
                language = input("Using XLM. Select language in " + str(list(available_languages)) + " >>> ")
        # kwargs["language"] = tokenizer.lang2id[language]

    # TODO fix mask_token_id setup when configurations will be synchronized between models and tokenizers
    # XLM masked-language modeling (MLM) models need masked token
    # is_xlm_mlm = "mlm" in args.model_name_or_path
    # if is_xlm_mlm:
    #     kwargs["mask_token_id"] = tokenizer.mask_token_id

    return prompt_text


def prepare_xlnet_input(args, _, tokenizer, prompt_text):
    prompt_text = (args.padding_text if args.padding_text else PADDING_TEXT) + prompt_text
    return prompt_text, {}


def prepare_transfoxl_input(args, _, tokenizer, prompt_text):
    prompt_text = (args.padding_text if args.padding_text else PADDING_TEXT) + prompt_text
    return prompt_text, {}


PREPROCESSING_FUNCTIONS = {
    "ctrl": prepare_ctrl_input,
    "xlm": prepare_xlm_input,
    "xlnet": prepare_xlnet_input,
    "transfo-xl": prepare_transfoxl_input,
}


def adjust_length_to_model(length, max_sequence_length):
    if length < 0 and max_sequence_length > 0:
        length = max_sequence_length
    elif 0 < max_sequence_length < length:
        length = max_sequence_length  # No generation bigger than model size
    elif length < 0:
        length = MAX_LENGTH  # avoid infinite loop
    return length


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model_type",
        default=None,
        type=str,
        required=True,
        help="Model type selected in the list: " + ", ".join(MODEL_CLASSES.keys()),
    )
    parser.add_argument(
        "--model_name_or_path",
        default=None,
        type=str,
        required=True,
        help="Path to pre-trained model or shortcut name selected in the list: " + ", ".join(MODEL_CLASSES.keys()),
    )

    parser.add_argument("--prompt", type=str, default="")
    parser.add_argument("--length", type=int, default=20)
    parser.add_argument("--stop_token", type=str, default=None, help="Token at which text generation is stopped")

    parser.add_argument(
        "--temperature",
        type=float,
        default=1,
        help="temperature of 1.0 has no effect, lower tend toward greedy sampling",
    )
    parser.add_argument(
        "--repetition_penalty", type=float, default=1.0, help="primarily useful for CTRL model; in that case, use 1.2"
    )
    parser.add_argument("--k", type=int, default=0)
    parser.add_argument("--p", type=float, default=0.9)

    parser.add_argument("--padding_text", type=str, default="", help="Padding text for Transfo-XL and XLNet.")
    parser.add_argument("--xlm_language", type=str, default="", help="Optional language when used with the XLM model.")

    parser.add_argument("--seed", type=int, default=42, help="random seed for initialization")
    parser.add_argument("--no_cuda", action="store_true", help="Avoid using CUDA when available")
    parser.add_argument("--no_file", action="store_true", help="Generate without file")
    parser.add_argument("--prompt_file",type=str, default="", help="Prompt text and the conditional label")
    parser.add_argument("--output_file", type=str, default="", help="Output directory")

    parser.add_argument("--use_fact", action="store_true", help="whether the input data contain the fact information")
    parser.add_argument("--fact_sep", action="store_true", help="whether the input data contain the fact information")
    parser.add_argument("--cond_gen", action="store_true", help="whether the input data contain the fact information")
    args = parser.parse_args()

    args.device = torch.device("cuda" if torch.cuda.is_available() and not args.no_cuda else "cpu")
    args.n_gpu = torch.cuda.device_count()

    set_seed(args)

    # Initialize the model and tokenizer
    try:
        args.model_type = args.model_type.lower()
        model_class, tokenizer_class = MODEL_CLASSES[args.model_type]
    except KeyError:
        raise KeyError("the model {} you specified is not supported. You are welcome to add it and open a PR :)")

    tokenizer = tokenizer_class.from_pretrained(args.model_name_or_path)
    model = model_class.from_pretrained(args.model_name_or_path)
    model.to(args.device)

    args.length = adjust_length_to_model(args.length, max_sequence_length=model.config.max_position_embeddings)
    logger.info(args)


    def file_gen(args):
        # prompt_list = open(args.prompt_file.format("src"),'r').readlines()
        # ref_list = open(args.prompt_file.format("tgt"),'r').readlines()
        # ref_content = [" ".join(i.strip().split()[:400]) for i in ref_list]
        data = pd.read_csv(args.prompt_file, sep="\t")
        prompt_list = data['title'].values.tolist()
        ref_list = data['content'].values.tolist()
        ref_content = [" ".join(i.strip().split()[:400]) for i in ref_list]

        # fact_list = ["<f-begin>" + " ".join((" ".join(i[-2].split("|"))).split(",")) + "<f-end>" for i in prompt_list]
        fact_raw = prompt_list

    
        encoded_prompt = [tokenizer.encode(prompt_text + " <c-begin> ".strip(), add_special_tokens=False,  return_tensors="pt")[:100] for prompt_text in
                          prompt_list]



        fout = open(args.output_file, 'w+')
        total = len(encoded_prompt)
        # iter = zip(encoded_prompt, prompt_labels_list)
        fout.write("prompt\tref_fact\tref\tgen\tstyle_label\n")
        with tqdm(total=total) as pbar:

            for i, e_p in enumerate(encoded_prompt):
                e_p = e_p.to(args.device)
                output_sequences = model.generate(
                        input_ids=e_p,
                        max_length=args.length,
                        temperature=args.temperature,
                        top_k=args.k,
                        top_p=args.p,
                        repetition_penalty=args.repetition_penalty,
                        do_sample=True
                    )



                generated_sequence = output_sequences[0].tolist()
                text = tokenizer.decode(generated_sequence, clean_up_tokenization_spaces=True)
                text = text[text.find("<c-begin>"): text.find(args.stop_token) if args.stop_token else None]
                text = text.replace("<pad>", "")
                text = text.replace("<c-begin>", "")
                text = text.replace("\n", " ")
                prompt_text = tokenizer.decode(e_p.cpu()[0].tolist(), clean_up_tokenization_spaces=True)

                fout.write("{}\t{}\t{}\t{}\t{}\n".format(prompt_text.strip(),fact_raw[i].strip(),ref_content[i].strip(),text.strip(), 1))
                pbar.update(1)
        fout.close()
    def prompt_gen():
        while True:
                    text = input("Prompt Text>>")
                    e_p = tokenizer.encode(text, add_special_tokens=False, return_tensors="pt")
                    e_p = e_p.to(args.device)
                    output_sequences = model.generate(
                        input_ids=e_p,
                        max_length=args.length,
                        temperature=args.temperature,
                        top_k=args.k,
                        top_p=args.p,
                        repetition_penalty=args.repetition_penalty,
                        do_sample=True
                    )

                    # Batch size == 1. to add more examples please use num_return_sequences > 1
                    generated_sequence = output_sequences[0].tolist()
                    text = tokenizer.decode(generated_sequence, clean_up_tokenization_spaces=True)
                    text = text[: text.find(args.stop_token) if args.stop_token else None]
                    text = text.replace("<pad>", "")
                    text = text.replace("\n", " ")
                    print(text)
    if args.no_file:
        prompt_gen()
    else:
        file_gen(args)

if __name__ == "__main__":
    main()
