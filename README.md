# Emojify

Tools for manipulating Slack emojis. Designed for use on the command-line and
use as a Slack slash command processed on AWS. Both the command-line and the
Slack slash-command have the same command-line arguments. If needed this code
can also be used as a Python module to construct more complicated scripts that
need to manipulate Emojis.

## Command-Line

To execue this on the commandline you'll need Python 3.6 and pipenv. To begin,
install the Pipenv using:

```
$ pipenv install --python 3.6
```

If you don't have python 3.6 then you'll need to install it, or have Pipenv
install it automatically by setting up pyenv.

The script requires several environment variables:

```
export EMOJFIY_TEAM_NAME=myteamname
export EMOJIFY_EMAIL=my@email.com
export EMOJIFY_PASSWORD=secretpassword
```

Unfortunately this requires your username/password because right now the Slack
Apps API has no way to call `emoji.add` or `emoji.remove`, so we must simulate
a user login rather than using a Slack Apps API key. Obviously since you're
exposing your password to this script, you might want to read the script to
make sure it isn't doing anything nasty.

Once the environment variables are set up, to run Emojify you can run the
following example commands, substituting your own values for the emoji names
and image URLs:

```
$ ./emojify add test_emoji http://testemoji.com/foo.jpg
$ ./emojify remove test_emoji
$ ./emojify alias test_emoji test_emoji_alias
```

The `emojify` command script is just a wrapper around the longer
`pipenv run python emojify.py`, which is what is really executed under the hood.

## AWS

To run as a slash-command on AWS takes a bit of setup that I eventually want to
encapsulate in a Terraform or CloudFormation script, but it is presently manual
configuration.

```
Slack --> API Gateway --> Dispatch Lambda --> SNS Topic --> Processing Lambda
  |                               |                                 |
  ^                               V                                 |
  \--<--- Initial Response ---<---/                                 V
  |                                                                 |
  ^                                                                 |
  \------------<------------ Final Response ------------<-----------/
```

Two lambdas are required because Slack expects a response to the slash command
in under three seconds, and it may take some time to fully upload the emoji.
The "dispatch" lambda validates some of the request, sends a message back to
the user (privately) that the command is being processed, and then puts an item
in an SNS topic. The SNS topic triggers the processing lambda to do the actual
processing of the command. Once command processing is complete, the processing
lambda sends a public message to the channel stating what happened, or if
processing failed a private message is sent back to the user describing the
error.

### Setup

This is not an absolute beginner's step-by-step guide. Familiarity with AWS is
assumed.

1. Build `emoji.zip` by running `make` in the repository root. This will
   package up all of the code in a way that can be uploaded to AWS Lambda.
2. Create a slack app using slack's web interface. Get the verification token.
3. Create the SNS topic. Get the SNS topic's ARN.
4. Create the Dispatch Lambda to use Python 3.6, giving it an API Gateway
   trigger, and access to SNS. The Handler should be `emojify.dispatch`. Two
   environment variables are required here, the `EMOJIFY_SLACK_TOKEN` and the
   `EMOJIFY_SNS_TOPIC` you retreived earlier. Finally, upload `emoji.zip` for
   the Lambda. The timeout should be 3 seconds.
5. Create the Processing Lambda int the same way, but the Trigger should be
   SNS and the Handler should be `emojify.emojify`. The timeout should be 30
   seconds, and the memory cap should probably be 256MB. The environment
   variables should be `EMOJIFY_EMAIL`, `EMOJIFY_PASSWORD`, and
   `EMOJIFY_TEAM_NAME`.
6. Create an API Gateway that passes through requests to the Dispatch Lambda.
7. Create a slash command in the Slack App, and give it the API Gateway's URL
   for the webhook.

## Python Module

The `emojify.py` module exposes several useful functions.

```
get_slack_client(url, email, password)
add_emoji(client, image, name)
remove_emoji(client, name)
alias_emoji(client, target, alias)
```

Use as a module is not the primary use case, and I'm too lazy to document it
better than that.
