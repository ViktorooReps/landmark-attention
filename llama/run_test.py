# Copyright 2023 Amirkeivan Mohtashami, Martin Jaggi
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

import torch

import os
import random
import re
import requests
import logging

import json


llama_weights_7b_base = "huggyllama/llama-7b"
llama_weights_7b_tuned = "/mloscratch/homes/mohtasha/llm/llama-redpajama-mem-8000-with-mem-triton-new-format"
cache_path = "/mloscratch/hf_cache/"
use_flash = True
offload_cache_to_cpu = False
top_k = 5
dtype = torch.bfloat16

def make_llama_base_pipe():

    from transformers import pipeline

    from transformers.models.llama import LlamaForCausalLM

    llama_base = LlamaForCausalLM.from_pretrained(
        llama_weights_7b_base,
        cache_dir=cache_path,
    )

    llama_base = llama_base.to('cuda')

    import transformers
    
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        llama_weights_7b_base,
        cache_dir=cache_path,
        model_max_length=None,
        padding_side="left",
        truncate=False,
        use_fast=False,
    )

    llama_base_pipe = pipeline("text-generation", model=llama_base, tokenizer=tokenizer, device=llama_base.device, use_cache=True, torch_dtype=torch.bfloat16, device_map="auto")
    return llama_base_pipe



# llama_base_pipe = make_llama_base_pipe()

def make_llama_mem_pipe():
    from llama_mem import LlamaForCausalLM

    model = LlamaForCausalLM.from_pretrained(
        llama_weights_7b_tuned,
        cache_dir=cache_path,
        torch_dtype=dtype
    )

    model.to('cuda')

    import transformers

    tokenizer = transformers.AutoTokenizer.from_pretrained(
            llama_weights_7b_tuned,
            cache_dir=cache_path,
            model_max_length=None,
            padding_side="right",
            use_fast=False,
        )
    mem_id = tokenizer.convert_tokens_to_ids("<landmark>")
    model.set_mem_id(mem_id)
    from transformers import pipeline
    llama_mem_pipe = pipeline("text-generation", model=model, tokenizer=tokenizer, device=model.device,
                              offload_cache_to_cpu=offload_cache_to_cpu, use_flash=use_flash, 
                              cache_top_k=top_k, device_map="auto", torch_dtype=torch.bfloat16, use_cache=True, aggregate="max_over_tokens")
    return llama_mem_pipe


llama_mem_pipe = make_llama_mem_pipe()



pipes = {"mem": llama_mem_pipe}  # {"base": llama_base_pipe, "mem": llama_mem_pipe}


def generate_prompt(n_garbage):
    """Generates a text file and inserts an execute line at a random position."""
    n_garbage_prefix = random.randint(0, n_garbage)
    n_garbage_suffix = n_garbage - n_garbage_prefix

    keys = [f'key_{i:04d}' for i in range(2001)]
    random.shuffle(keys)

    information_key, keys = keys[0], keys[1:]
    
    task_description = "There is an important info hidden inside a lot of irrelevant text. Find it and memorize them. I will quiz you about the important information there."
    garbage = "The grass is green. The sky is blue. The sun is yellow. Here we go. There and back again."
    garbage_inf = " ".join([garbage + f"The {keys[i]} is {random.randint(1_000_000, 9_999_999)}" for i in range(2000)])
    assert len(garbage_inf) >= n_garbage
    garbage_prefix = garbage_inf[:n_garbage_prefix]
    garbage_suffix = garbage_inf[:n_garbage_suffix]
    pass_key = random.randint(1_000_000, 9_999_999)
    information_line = f"The {information_key} is {pass_key}. "
    final_question = f"What is the {information_key}? The {information_key} is"
    lines = [
        task_description,
        garbage_prefix,
        information_line,
        garbage_suffix,
        final_question
    ]
    return "\n".join(lines), information_key, pass_key
            

@torch.no_grad()
def test_model(prompt_text, information_key, pass_key, model_name):
    response = pipes[model_name](prompt_text,num_return_sequences=1, max_new_tokens=30)[0]["generated_text"][len(prompt_text):]
    assert f"The {information_key} is {pass_key}. " in prompt_text

    try:
        pass_key = int(re.search(r'\d+', response).group())
    except:
        pass_key = response[:20]
    
    return pass_key


n_values = [2000]
num_tests = 50 #50
# models = ["base", "mem"]
models = list(pipes.keys())
accuracies = {x: [] for x in models}
individual_results = {x: [] for x in models}
n_tokens = {x: [] for x in models}

for n in n_values:
    
    correct_count = {x: 0 for x in models}
    
    n_results = {x: [] for x in models}
    for i in range(num_tests):
        print(f"\nRunning test {i + 1}/{num_tests} for n = {n}...")
        prompt_text, information_key, pass_key = generate_prompt(n)
        
        
        try:
            for model_name in models:
                if pipes[model_name] is None:
                    continue
                num_tokens = len(pipes[model_name].tokenizer.encode(prompt_text))

                print("Number of tokens in this prompt: ", num_tokens)
                n_tokens[model_name].append(num_tokens)
                model_output = test_model(prompt_text, information_key, pass_key, model_name)
                print(f"Expected number in the prompt: {pass_key}, {model_name} output: {model_output}")

                if pass_key == model_output:
                    correct_count[model_name] += 1
                    n_results[model_name].append(1)
                    print("Success!")
                else:
                    n_results[model_name].append(0)
                    print("Fail.")
        except Exception as e:
            logging.warning('Exception!', exc_info=e)
            break
    
    for model in models:
        accuracy = (correct_count[model] / num_tests) * 100
        print(f"Accuracy {model} for n = {n}: {accuracy}%")
        accuracies[model].append(accuracy)
        individual_results[model].append(n_results)


with open('individual_results.json', 'w') as f:
    json.dump(individual_results, f, indent=2)


with open('accuracies.json', 'w') as f:
    json.dump(accuracies, f, indent=2)


with open('n_tokens.json', 'w') as f:
    json.dump(n_tokens, f, indent=2)