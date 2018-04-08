#!/usr/bin/env python

# Copyright 2017 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Google Cloud Speech API sample application using the streaming API.

NOTE: This module requires the additional dependency `pyaudio`. To install
using pip:

    pip install pyaudio

Example usage:
    python transcribe_streaming_mic.py
"""

# [START import_libraries]
from __future__ import division

import re
import sys

from google.cloud import speech
from google.cloud.speech import enums
from google.cloud.speech import types
import pyaudio
from six.moves import queue
from Tkinter import *
import Tkinter
import turtle
import time
import tkFont
from collections import OrderedDict
# [END import_libraries]

master = Tk()

w = Canvas(master, width=400, height=400)
w.pack()

stack = []
word_queue = OrderedDict()
functions = ["+", "-", "*", "/", "%", "push", "eval", "reset"]

def draw():

    wordsX = 20
    wordsY = 395

    currentX = 350
    currentY = 400

    global w
    w.delete("all")

    w.create_text(20, 380, text = "Current Word Queue:")
    w.create_text(275, 200, text = "Current Stack:")

    # for word in word_queue:
    #     w.create_text(wordsX, wordsY, text = word)
    #     wordsX += 25

    for word in word_queue.keys():
        if word_queue[word] == True:
            color = "black"
            overstrike = 0
        else:
            color = "red"
            overstrike = 1

        font = tkFont.Font(overstrike = overstrike)
        w.create_text(wordsX, wordsY, text = word, fill = color, font = font)
        wordsX += 25

    for item in stack:
        w.create_rectangle(currentX, currentY, currentX + 50, currentY - 50, fill="blue")
        w.create_text(currentX + 20, currentY - 25, text = item, fill = "black")
        currentY -= 51

    w.update_idletasks()
    w.update()

# Audio recording parameters
RATE = 16000
CHUNK = int(RATE / 10)  # 100ms

class MicrophoneStream(object):
    """Opens a recording stream as a generator yielding the audio chunks."""
    def __init__(self, rate, chunk):
        self._rate = rate
        self._chunk = chunk

        # Create a thread-safe buffer of audio data
        self._buff = queue.Queue()
        self.closed = True

    def __enter__(self):
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = self._audio_interface.open(
            format=pyaudio.paInt16,
            # The API currently only supports 1-channel (mono) audio
            # https://goo.gl/z757pE
            channels=1, rate=self._rate,
            input=True, frames_per_buffer=self._chunk,
            # Run the audio stream asynchronously to fill the buffer object.
            # This is necessary so that the input device's buffer doesn't
            # overflow while the calling thread makes network requests, etc.
            stream_callback=self._fill_buffer,
        )

        self.closed = False

        return self

    def __exit__(self, type, value, traceback):
        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self.closed = True
        # Signal the generator to terminate so that the client's
        # streaming_recognize method will not block the process termination.
        self._buff.put(None)
        self._audio_interface.terminate()

    def _fill_buffer(self, in_data, frame_count, time_info, status_flags):
        """Continuously collect data from the audio stream, into the buffer."""
        self._buff.put(in_data)
        return None, pyaudio.paContinue

    def generator(self):
        while not self.closed:
            # Use a blocking get() to ensure there's at least one chunk of
            # data, and stop iteration if the chunk is None, indicating the
            # end of the audio stream.
            chunk = self._buff.get()
            if chunk is None:
                return
            data = [chunk]

            # Now consume whatever other data's still buffered.
            while True:
                try:
                    chunk = self._buff.get(block=False)
                    if chunk is None:
                        return
                    data.append(chunk)
                except queue.Empty:
                    break

            yield b''.join(data)
# [END audio_stream]

def convert_keyword(value):
    if value == "plus" or value == "add":
        return "+"
    elif value == "minus" or value == "subtract" or value == "sub":
        return "-"
    elif value == "times" or value == "multiply":
        return "*"
    elif value == "divide":
        return "/"
    elif value == "modulus" or value == "mod":
        return "%"
    else:
        return value

def is_int(value):
    try:
        int(value)
        return True
    except:
        return False

def is_function(value):
    global functions
    return (value in functions)

def eval_top(stack):
    """
    Evaluates based on the top item of the stack.
    If this is a number, the number gets returned.
    If this is a function, the function gets evaluated with the result
    put on the top of the stack.
    """
    if len(stack) > 0:
        top = stack.pop()
        if is_int(top):
            return int(top)
        elif is_function(top):
            res = None
            if top == "+":
                res = eval_top(stack) + eval_top(stack)
            elif top == "-":
                res = eval_top(stack) - eval_top(stack)
            elif top == "*":
                res = eval_top(stack) * eval_top(stack)
            elif top == "/":
                res = eval_top(stack) / eval_top(stack)
            elif top == "%":
                res = eval_top(stack) % eval_top(stack)
            else:
                print("ERROR: invalid function, somehow.")
            return res
        else:
            print("ERROR: invalid word. continuing with top of stack.")
            eval_top(stack)
    else:
        print("ERROR: not enough arguments for eval.")

def listen_print_loop(responses):
    """Iterates through server responses and prints them.

    The responses passed is a generator that will block until a response
    is provided by the server.

    Each response may contain multiple results, and each result may contain
    multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
    print only the transcription for the top alternative of the top result.

    In this case, responses are provided for interim results as well. If the
    response is an interim one, print a line feed at the end of it, to allow
    the next result to overwrite it, until the response is a final one. For the
    final one, print a newline to preserve the finalized transcription.
    """
    global stack
    global word_queue
    num_chars_printed = 0
    for response in responses:
        if not response.results:
            continue

        # The `results` list is consecutive. For streaming, we only care about
        # the first result being considered, since once it's `is_final`, it
        # moves on to considering the next utterance.
        result = response.results[0]
        if not result.alternatives:
            continue

        # Display the transcription of the top alternative.
        transcript = result.alternatives[0].transcript

        # Display interim results, but with a carriage return at the end of the
        # line, so subsequent lines will overwrite them.
        #
        # If the previous result was longer than this one, we need to print
        # some extra spaces to overwrite the previous result
        overwrite_chars = ' ' * (num_chars_printed - len(transcript))

        if not result.is_final:
            sys.stdout.write(transcript + overwrite_chars + '\r')
            sys.stdout.flush()

            num_chars_printed = len(transcript)

        else:
            print(transcript + overwrite_chars)
            words = transcript.split()
            words = [convert_keyword(word.lower()) for word in words]
            for word in words:
                word_queue[word] = False
            words = [word for word in words if is_int(word) or is_function(word)]
            if len(words) > 0:
                if words[-1] == "push":
                    words.pop()
                    for word in word_queue.keys():
                        print "this is a key: " + word + "\n"
                        if word_queue[word]:
                            stack.append(word)
                    word_queue = OrderedDict()
                elif words[-1] == "eval":
                    words.pop()
                    stack.append(str(eval_top(stack)))
                elif words[-1] == "reset":
                    word_queue = OrderedDict()
                else:
                    for word in words:
                        word_queue[word] = True;
                    #word_queue = word_queue + words
                print("Stack: " + str(stack))
                print("Word Queue: " + str(word_queue))

            # Exit recognition if any of the transcribed phrases could be
            # one of our keywords.
            if re.search(r'\b(exit|quit)\b', transcript, re.I):
                print('Exiting..')
                break

            num_chars_printed = 0
        draw()
        for word in word_queue.keys():
            if word_queue[word] == False:
                word_queue.pop(word,None)


def main():
    # See http://g.co/cloud/speech/docs/languages
    # for a list of supported languages.
    language_code = 'en-US'  # a BCP-47 language tag

    client = speech.SpeechClient()
    config = types.RecognitionConfig(
        encoding=enums.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=RATE,
        language_code=language_code)
    streaming_config = types.StreamingRecognitionConfig(
        config=config,
        interim_results=True)

    with MicrophoneStream(RATE, CHUNK) as stream:
        audio_generator = stream.generator()
        requests = (types.StreamingRecognizeRequest(audio_content=content)
                    for content in audio_generator)

        responses = client.streaming_recognize(streaming_config, requests)

        # Now, put the transcription responses to use.
        listen_print_loop(responses)


if __name__ == '__main__':
    main()
