"""Manipulates slack emojis. Designed to run as either a command-line
application or as an AWS Lambda pair."""

import argparse
import json
import os
import re
import shlex
import sys
import traceback
import urllib.parse
import urllib.request

import boto3
import bs4
import requests
import slackclient
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

def get_slack_client(url, email, password):
    """Instantiates a SlackClient using a token we get through login."""

    session = requests.session()
    response = session.get(url)
    response.raise_for_status()
    soup = bs4.BeautifulSoup(response.text, "html.parser")
    crumb = soup.find("input", attrs={"name": "crumb"})["value"]
    data = {'signin': 1,
            'redir': '/customize/emoji',
            'crumb': crumb,
            'remember': 'on',
            'email': email,
            'password': password}
    response = session.post(url, data=data)
    response.raise_for_status()
    api_token_regex = re.compile('"api_token":"([a-z0-9-]*)",')
    api_token = api_token_regex.search(response.text).groups()[0]
    return slackclient.SlackClient(api_token)

def get_slack_client_from_env():
    """Instantiates a slack client using a team name, email, and password
    retreived from environment variables."""

    url = 'https://{}.slack.com'.format(os.environ['EMOJIFY_TEAM_NAME'])
    email = os.environ['EMOJIFY_EMAIL']
    password = os.environ['EMOJIFY_PASSWORD']
    return get_slack_client(url, email, password)

def assert_emoji(client, exists=None, not_exists=None):
    """Makes sure a particular emoji exists and/or does not exist."""

    emojis = client.api_call('emoji.list')
    if 'ok' not in emojis or not emojis['ok'] or 'emoji' not in emojis:
        raise Exception('failed to call emoji.list')
    if exists and exists not in emojis['emoji']:
        raise Exception('Emoji {} does not exist.'.format(exists))
    if not_exists and not_exists in emojis['emoji']:
        raise Exception('Emoji {} already exists.'.format(not_exists))

def add_emoji(client, image, name):
    """Uploads an image file as an emoji to Slack. The image is expected to be
    at most 128x128 and 64k."""

    # Use requests rather than the slack client because the syntax to make the
    # SlackClient upload the image is unclear.
    assert_emoji(client, not_exists=name)
    data = {'mode': 'data', 'name': name, 'token': client.token}
    files = {'image': ('emoji_filename', image.make_blob(), image.mimetype)}
    response = requests.post('https://slack.com/api/emoji.add',
                             data=data,
                             files=files)
    response.raise_for_status()

def remove_emoji(client, name):
    """Deletes an emoji by name."""
    assert_emoji(client, exists=name)
    client.api_call('emoji.remove', name=name)

def alias_emoji(client, target, alias):
    """Aliases an emoji such that alias is the same emoji as target."""
    assert_emoji(client, exists=target, not_exists=alias)
    client.api_call('emoji.add', mode='alias', name=alias, alias_for=target)

def handle_add(client, args):
    """Handle a create command, which has 'url' and 'name' arguments."""
    image_file = urllib.request.urlopen(args.url)
    with wand.image.Image(file=image_file) as image:
        square_crop_and_resize(image, 128)
        add_emoji(client, image, args.name)
    return 'Created emoji {0} :{0}:'.format(args.name)

def handle_remove(client, args):
    """Handle a delete command, which has just a 'name' argument."""
    remove_emoji(client, args.name)
    return 'Deleted emoji {}'.format(args.name)

def handle_alias(client, args):
    """Handle an alias command, which has 'name' and 'target' arguments."""
    alias_emoji(client, args.target, args.alias)
    return 'Aliased emoji {} links to {}'.format(args.alias, args.target)

def parse_command_line(cmdline=None):
    """Parse the slack slash-command command-line arguments."""
    parser = ArgumentParser()
    subparsers = parser.add_subparsers()
    create_parser = subparsers.add_parser('add')
    create_parser.add_argument('name')
    create_parser.add_argument('url')
    create_parser.set_defaults(func=handle_add)
    delete_parser = subparsers.add_parser('remove')
    delete_parser.add_argument('name')
    delete_parser.set_defaults(func=handle_remove)
    alias_parser = subparsers.add_parser("alias")
    alias_parser.add_argument('target')
    alias_parser.add_argument('alias')
    alias_parser.set_defaults(func=handle_alias)
    if cmdline:
        args = parser.parse_args(shlex.split(cmdline))
    else:
        args = parser.parse_args()
    return args

def emojify(event, _):
    """Entry point for the lambda that actually does the processing."""
    try:

        # Process the SNS message.
        print('SNS MESSAGE: {}'.format(event['Records'][0]['Sns']['Message']))
        message = json.loads(event['Records'][0]['Sns']['Message'])
        user_id = message['user_id']
        response_url = message['response_url']

        # Process the command.
        print('COMMAND LINE: ' + message['command'])
        args = parse_command_line(message['command'])
        print('COMMAND BEGIN')
        success_message = '{}, thanks <@{}>!'.format(
            args.func(get_slack_client_from_env(), args), user_id)
        print('COMMAND COMPLETE: ' + success_message)
        requests.post(response_url,
                      data=json.dumps({'text': success_message,
                                       'response_type': 'in_channel'}))
    # pylint: disable=broad-except
    except Exception as exc:
        print('COMMAND ERROR: ' + str(exc))
        traceback.print_exc()
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
                                     '/emojify add [name] [url]\n' +
                                     '/emojify remove [name]\n' +
                                     '/emojify alias [target] [alias]')
        command = params['text'][0]
        print('DISPATCH COMMAND: ' + command)

        # Parse the command-line arguments here even though the next lambda
        # also parses them. This allows us to respond immediately to ill-formed
        # commands.
        parse_command_line(command)

        # Publish an SNS notification to invoke the second-state lambda.
        message = {
            "response_url": params['response_url'][0],
            "user_id": params['user_id'][0],
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
        print('DISPATCH ERROR: ' + str(exc))
        traceback.print_exc()
        return generate_response(str(exc))
    # pylint: enable=broad-except

def main():
    """Process the command given on the command line."""
    args = parse_command_line()
    print(args.func(get_slack_client_from_env(), args))

if __name__ == '__main__':
    main()
