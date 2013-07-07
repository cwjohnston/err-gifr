from errbot import BotPlugin, botcmd
from os import path, makedirs, remove
from StringIO import StringIO
from hashlib import sha1
from PIL import Image
from base64 import b64encode
from shutil import rmtree

import logging
import requests
import json
import subprocess
import random


class Gifr(BotPlugin):
    """An Err plugin for randomizing animated gifs"""
    min_err_version = '1.6.0'  # Optional, but recommended
    max_err_version = '2.0.0'  # Optional, but recommended

    def ensure_cache_dir(self):
        """Ensure the configured CACHE_PATH and necessary subdirectories exist"""
        if self.config and ('CACHE_PATH' in self.config):
            cache_root = self.config['CACHE_PATH']
            if not path.exists(cache_root):
                makedirs(cache_root)

    def get_configuration_template(self):
        """Defines the configuration structure this plugin supports"""
        return {'GIFSICLE_PATH': '/usr/bin/gifsicle', 'CACHE_PATH': "/tmp/gifr", 'IMGUR': {'CLIENT_ID': "***change me***", 'API_KEY': "*** me too ***"}}

    def is_animated(self, img):
        try:
            img.seek(1)
        except EOFError:
            return False
        else:
            return True

    def count_frames(self, img):
        count = 0
        while img:
            count += 1
            try:
                img.seek(count)
            except EOFError:
                break
        return count - 1

    def int_to_frame_string(self, i):
        """Returns a string formatted for use as a frame number in a gifsicle command"""
        return "\"#%s\"" % (i,)

    def randomize_gif(self, source_url, img, frame_count):
        """Calls out to gifsicle to do the randomization, returns the path to the randomized file"""
        hashed_name = sha1(source_url).hexdigest()
        exe = self.config['GIFSICLE_PATH']
        frames = range(frame_count)
        random.shuffle(frames)
        outfile = path.join(self.config['CACHE_PATH'], "%s.gif" % (hashed_name,))
        command = "%s --colors=255 | %s --unoptimize | %s -O3 %s > %s" % (exe, exe, exe, " ".join(map(self.int_to_frame_string, frames)), outfile,)
        logging.debug("Gifr: generated command line: \n    %s" % (command,))
        p = subprocess.Popen(["-c", command], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, bufsize=4096)
        p.stdin.write(img.getvalue())
        p.stdin.close()
        p.wait()
        img.close()
        return outfile

    def imgur_upload(self, file_path, name):
        """Uploads image file to imgur via REST API"""
        hashed_name = sha1(name).hexdigest()
        client_id = self.config['IMGUR']['CLIENT_ID']
        api_key = self.config['IMGUR']['API_KEY']

        response = requests.post(
            "https://api.imgur.com/3/upload",
            headers={"Authorization": "Client-ID %s" % (client_id,)},
            data={
                'key': api_key,
                'image': b64encode(open(file_path, 'rb').read()),
                'type': 'base64',
                'name': hashed_name
            }
        )

        logging.debug("Gifr imgur response: %s" % (response.json(),))
        return response

    def add_to_cache(self, source_url, result_url):
        cache = self.get('gifr_cache', [])
        cache.append({'source': source_url, 'result': result_url})
        self['gifr_cache'] = cache
        return True

    # Passing split_args_with=None will cause arguments to be split on any kind
    # of whitespace, just like Python's split() does
    @botcmd(split_args_with=None)
    def gifr(self, mess, args):
        """Takes a URL as it's only argument, returns a URL to the randomized gif"""
        self.ensure_cache_dir()

        if not args:
            return "Usage: !gifr IMAGE_URL"

        source_url = args[0]
        cache = self.get('gifr_cache', [])

        logging.debug("Gifr: Cache contains %s entries" % len(cache))

        for gif in cache:
            logging.debug("Gifr: inspecting cache for %s" % (source_url,))
            logging.debug("Gifr: saw source url %s" % (gif['source'],))
            if gif['source'] == source_url:
                logging.debug("Gifr: matched %s to %s in cache" % (source_url, gif['result'],))
                return gif['result']

        response = requests.get(source_url, verify=False)

        try:
            img = Image.open(StringIO(response.content))
        except IOError as e:
            return "That doesn't appear to be an image: %s" % (e,)

        frame_count = self.count_frames(img)

        if frame_count <= 0:
            return "Sorry, that doesn't appear to be animated"
        else:
            logging.debug("Gifr: Counted %s frames" % (frame_count,))

        result_file = self.randomize_gif(source_url, StringIO(response.content), frame_count)
        imgur_response = self.imgur_upload(result_file, source_url)
        if imgur_response.status_code is 200:
            result_url = imgur_response.json()['data']['link']
            self.add_to_cache(source_url, result_url)
            return result_url
        else:
            return "There was a problem uploading to imgur: %s" % (imgur_response.json())

    @botcmd(split_args_with=None)
    def gifr_gimme(self, mess, args):
        """Returns N random links from the cache"""
        if not args:
            return "Usage: !gifr gimme N"

        cache = self.get('gifr_cache', [])
        sample = random.sample(cache, args[0])
        return json.dumps(sample)

    @botcmd(split_args_with=None)
    def gifr_spew(self, mess, args):
        """Spews the cache"""
        return json.dumps(self.get('gifr_cache', []))

    @botcmd(split_args_with=None)
    def gifr_zap(self, mess, args):
        """Zaps one or all of the entries in the cache"""

        if not args:
            return "Please specify a source url or 'all'"

        if args[0] == 'all':
            try:
                rmtree(self.config['CACHE_PATH'])
            except OSError:
                pass
            finally:
                self['gifr_cache'] = []
                return 'The cache is now empty'

        else:
            source_url = args[0]
            cache = self.get('gifr_cache')
            for gif in cache:
                if gif['source'] == source_url:
                    hashed_name = sha1(gif['source']).hexdigest()
                    file_name = path.join(self.config['CACHE_PATH'], "%s.gif" % (hashed_name,))
                    try:
                        remove(file_name)
                    except OSError:
                        pass
                    finally:
                        cache.remove(gif)
                        self['gifr_cache'] = cache
                        return '%s has been zapped from the cache' % (gif['source'],)
