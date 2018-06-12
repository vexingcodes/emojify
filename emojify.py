"""Manipulates slack emojis."""

import argparse
import json
import os
import re
import shlex
import sys
import time
import urllib.request

import boto3
import bs4
import requests
import wand.image

class ArgumentParser(argparse.ArgumentParser):
    """Make a subclass of argparse.ArgumentParser to override the stupid
    behavior of exiting the program when arguments fail to parse, so we can
    return an error message instead of just dying."""

    def error(self, message):
        """Raise an exception instead of terminating."""
        exc = sys.exc_info()[1]
        if exc:
            raise exc
        else:
            raise Exception('Error: {}'.format(message))

def square_crop_and_resize(image, size):
    """Crops an image to be square by removing pixels evenly from both sides of
    the longest side of the image. Then the image is resized to the desired
    size"""

    # Calculate how much longer the longest side is than the shortest.
    extra = max(image.width, image.height) - min(image.width, image.height)

    # Remove pixels evenly from the left or top.
    rem_lt = extra // 2

    # Remove pixels evenly from the right or bottom. We may need to take
    # another single pixel from one side if there is an uneven number of pixels
    # to split between the two sides.
    rem_rb = rem_lt + extra % 2

    # Crop the image centered so the image is square.
    if image.width > image.height:
        image.crop(rem_lt, 0, image.width - rem_rb - 1, image.height - 1)
    else:
        image.crop(0, rem_lt, image.width - 1, image.height - rem_rb - 1)
    assert image.width == image.height

    image.resize(size, size)

def emoji_session(url, email, password):
    """Creates a Slack session on the customize emoji page."""

    session = requests.session()

    # Get the login page and extract the "crumb" from it.
    response = session.get(url)
    response.raise_for_status()
    soup = bs4.BeautifulSoup(response.text, "html.parser")
    crumb = soup.find("input", attrs={"name": "crumb"})["value"]

    # Issue a login request, and get the next "crumb".
    data = {'signin': 1,
            'redir': '/customize/emoji',
            'crumb': crumb,
            'remember': 'on',
            'email': email,
            'password': password}
    response = session.post(url, data=data)
    response.raise_for_status()
    soup = bs4.BeautifulSoup(response.text, "html.parser")
    crumb = soup.find("input", attrs={"name": "crumb"})["value"]

    return (session, crumb, response.text)

def upload_emoji(image, name, url, email, password):
    """Uploads an image file as an emoji to Slack. The image is expected to be
    at most 128x128 and 64k."""

    (session, crumb, response_text) = emoji_session(url, email, password)

    if ':{}:'.format(name) in response_text:
        raise Exception('Emoji {} already exists.'.format(name))

    # Issue an add emoji request.
    data = {'add': 1,
            'crumb': crumb,
            'name': name,
            'mode': 'data',
            'alias': '',
            'resized': ''}
    files = {'img': ('emoji_filename', image.make_blob(), image.mimetype)}
    response = session.post(url + '/customize/emoji',
                            data=data,
                            files=files)
    response.raise_for_status()

def delete_emoji(name, url, email, password):
    """Deletes an emoji by name."""

    (session, _, response_text) = emoji_session(url, email, password)

    if not ':{}:'.format(name) in response_text:
        raise Exception('Emoji {} does not exist.'.format(name))

    version_uid_regex = re.compile('version_uid: "([a-f0-9]*)",')
    api_token_regex = re.compile('api_token: "([a-z0-9-]*)",')
    version_uid = version_uid_regex.search(response_text).groups()[0][0:8]
    api_token = api_token_regex.search(response_text).groups()[0]
    xid = "{0}-{1:.3f}".format(version_uid, float(time.time()))
    delete_url = url + '/api/emoji.remove?_x_id={}'.format(xid)
    files = {'name': (None, name), 'token': (None, api_token)}
    response = session.post(delete_url, files=files)
    response.raise_for_status()

def alias_emoji(name, target, url, email, password):
    """Aliases an emoji such that name is the same emoji as target."""

    (session, crumb, response_text) = emoji_session(url, email, password)

    if ':{}:'.format(name) in response_text:
        raise Exception('Emoji {} already exists.'.format(name))
    if not ':{}:'.format(target) in response_text:
        raise Exception('Emoji {} does not exist.'.format(target))

    files = {'add':     (None, '1'),
             'crumb':   (None, crumb),
             'name':    (None, name),
             'mode':    (None, 'alias'),
             'alias':   (None, target),
             'resized': (None, '')}
    response = session.post(url + '/customize/emoji', files=files)
    response.raise_for_status()

def handle_create(args, url, email, password):
    """Handle a create command, which has 'url' and 'name' arguments."""
    image_file = urllib.request.urlopen(args.url)
    with wand.image.Image(file=image_file) as image:
        square_crop_and_resize(image, 128)
        upload_emoji(image, args.name, url, email, password)
    return 'Created emoji {} :{}:'.format(args.name, args.name)

def handle_delete(args, url, email, password):
    """Handle a delete command, which has just a 'name' argument."""
    delete_emoji(args.name, url, email, password)
    return 'Deleted emoji {}'.format(args.name)

def handle_alias(args, url, email, password):
    """Handle an alias command, which has 'name' and 'target' arguments."""
    alias_emoji(args.name, args.target, url, email, password)
    return 'Aliased emoji {} links to {}'.format(args.name, args.target)

def parse_command_line(cmdline):
    """Parse the slack slash-command command-line arguments."""
    parser = ArgumentParser()
    subparsers = parser.add_subparsers()
    create_parser = subparsers.add_parser('create')
    create_parser.add_argument('name')
    create_parser.add_argument('url')
    create_parser.set_defaults(func=handle_create)
    delete_parser = subparsers.add_parser('delete')
    delete_parser.add_argument('name')
    delete_parser.set_defaults(func=handle_delete)
    alias_parser = subparsers.add_parser("alias")
    alias_parser.add_argument('name')
    alias_parser.add_argument('target')
    alias_parser.set_defaults(func=handle_alias)
    args = parser.parse_args(shlex.split(cmdline))
    return args

def emojify(event, _):
    """Entry point for the lambda that actually does the processing."""
    try:
        # Process the environment variables.
        url = 'https://{}.slack.com'.format(os.environ['EMOJIFY_TEAM_NAME'])
        email = os.environ['EMOJIFY_EMAIL']
        password = os.environ['EMOJIFY_PASSWORD']

        # Process the SNS message.
        message = json.loads(event['Records'][0]['Sns']['Message'])
        response_url = message['response_url']

        # Process the command.
        print('COMMAND LINE: ' + message['command'])
        args = parse_command_line(message['command'])
        print('COMMAND BEGIN')
        message = args.func(args, url, email, password)
        print('COMMAND COMPLETE: ' + message)
        requests.post(response_url,
                      data=json.dumps({'text': message,
                                       'response_type': 'in_channel'}))
    # pylint: disable=broad-except
    except Exception as exc:
        print('COMMAND ERROR: ' + str(exc))
        requests.post(response_url, data=json.dumps({'text': str(exc)}))
    # pylint: enable=broad-except

def dispatch(event, _):
    """Entry point for the initial lambda. Just posts so an SNS topic to invoke
    the lambda that actually does the work. This is annoying, but the
    processing can take more than 3 seconds, which is the response time limit
    for slack."""

    def generate_response(message):
        """Generate a full HTTP JSON response."""
        return {
            'statusCode': str(200),
            'body': json.dumps({'text': message}),
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            }
        }

    try:
        params = urllib.parse.parse_qs(event['body'])
        if params['token'][0] != os.environ['EMOJIFY_SLACK_TOKEN']:
            raise Exception('Error validating slack token...')

        if 'text' not in params or not params['text']:
            return generate_response('Usage:\n' +
                                     '/emojify create [name] [url]\n' +
                                     '/emojify delete [name]\n' +
                                     '/emojify alias [name] [target]')
        command = params['text'][0]

        # Parse the command-line arguments here even though the next lambda
        # also parses them. This allows us to respond immediately to ill-formed
        # commands.
        parse_command_line(command)

        # Publish an SNS notification to invoke the second-state lambda.
        message = {
            "response_url": params['response_url'][0],
            "command": command
        }
        response = boto3.client('sns').publish(
            TopicArn=os.environ['EMOJIFY_SNS_TOPIC'],
            Message=json.dumps({'default': json.dumps(message)}),
            MessageStructure='json'
        )
        print('SNS PUBLISH: ' + str(response))

        return generate_response('Processing command "{}"...'.format(command))
    # pylint: disable=broad-except
    except Exception as exc:
        return generate_response(str(exc))
    # pylint: enable=broad-except
