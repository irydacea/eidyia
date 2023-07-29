# Eidyia - Wesnoth Site Status Service Stateful Support Survey Bot

`eidyia` is a Discord.py bot that performs Wesnoth.org site status monitoring
and reporting.

Most of the hard work is done by [`valen`](https://github.com/wesnoth/valen),
which has its own Web front-end publicly available at <https://status.wesnoth.org/>.
The focus of `eidyia` is reporting any changes to the site status report
directly to administrative areas on Wesnoth's official Discord guild and IRC
channels.

## Why this

You probably do not need this bot per se, but you may find it useful to adopt
and adapt parts for your own projects along with the `valen` infrastructure.
Most Wesnoth-specific parts of `eidyia` are easily customisable or even
configurable through the main JSON config file.

### Why the name

[Eidyia](https://en.wikipedia.org/wiki/Idyia) is the name of an Oceanid, from
Greek mythology. I intentionally chose the most annoying spelling to make as
many Wesnoth developers' lives annoying as possible. They think they like C++
for some reason, after all.

I like to give my projects codenames during early production so I can
procrastinate for as long as possible on coming up with a catchy name.
Sometimes the codenames stick around long enough that they become the final
production names; sometimes they don't.

### Why the overly long full name

`valen` was internally `WSS` (Wesnoth Status Service) when I began writing it.
I decided having the next component be `WSSSSSS` (or `WS6` for short) would be
appropriate.

### Why Discord / Why IRC

IRC definitely has a lot of advantages over proprietary platforms, but its
disadvantages are more visible to the average user. At the end of the day, I
do not have the energy to proselytise for either camp, I just want to make a
working status notification bot.

## Installation

The minimum required interpreter version is **Python 3.9**.

There are a couple of required dependencies that need to be installed first
for `eidyia` to run correctly:

```
python3 -m pip install -U discord.py ircrobots jsonc_parser watchdog
```

## Configuration

See the included `eidyia_example.jsonc` file for configuration instructions
and examples. The file should be renamed to `eidyia.jsonc` and put in the same
directory as the `eidyia` executable. An alternative option is to specify an
explicit path to the configuration file by invoking the bot with the `-c`
option.

**Before running**, you should make sure that at least one of the two client
modules are properly configured — that is, either Discord, or IRC.

* If using Discord, you must set up the bot on a Discord account, obtain a
  client token, and add it to the configuration file, and join the bot to the
  desired guilds. More information on this can be found in the next section.

* If using IRC, you should gather any applicable network login information
  that the bot needs. Beware that currently, the bot's only mechanism for
  authenticating admins on IRC relies on the connected network supporting
  [IRCv3 account-tag](https://ircv3.net/specs/extensions/account-tag), as well
  as bot admins having valid services accounts on that network. Additionally,
  it is worth noting the bot supports SASL authentication for itself and it
  may even be required by the network for safety reasons.

`eidyia` supports a few command line arguments:

* `-d / --debug`
  Increases log verbosity for hacking and debugging. It can be specified up to
  three times for additional debugging information (level 1: general, level 2:
  asyncio, level 3: Discord and IRC trace information).

* `-c CONFIG_FILE / --config CONFIG_FILE`
  Specifies the path to the Eidyia JSON config file.

* `-C CLIENTS / --client CLIENTS` 
  Specifies a comma-separated list of client modules that should be enabled if
  they are correctly configured in the JSON config file. By default this is
  `discord,irc`.

* `-r REPORT_FILE / --report REPORT_FILE`
  Specifies the path to the Valen report file. The default path used if this
  is not provided in the command line is `./valen.json`.

### Generating a bot token

1. Go into Discord’s [Developer Portal](https://discordapp.com/developers/applications/me)
   and create a new Application.

2. After creating your new Application with the desired name, description and
   icon, on the left pane choose Bot, then choose Add Bot, then confirm the
   action.

3. The first time you see the Bot page, you will see the option to Copy the
   generated token to clipboard. **If you navigate away from this page before
   copying the token, you will need to Reset it to a new one first,
   invalidating the previous token.**

4. Paste the token into the configuration file as the `token` option.

### Joining the bot to guilds

1. Return to the [Developer Portal](https://discordapp.com/developers/applications/me)
   and select the bot’s parent application from the My Applications section.

2. In the application’s General Information page, scroll down to the
   Application ID and choose Copy.

3. In order to join the bot to a guild, replace the Application ID into the
   `APPID` portion of the URL below, and visit it as a user with the guild
   Administrator privilege:

```
https://discordapp.com/oauth2/authorize?&client_id=APPID&scope=bot&permissions=51200
```
