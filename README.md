# Matrix Bot for Keepin track of element polls.
# Used Python 3.8.10
# Created using the matrix-nio python api for matrix communications
# Base template used: nio-template

#### FUNCTIONS ####
# This bot is designed to track user votes on a poll and display them live

######### Sources ##########
# bot template:       https://github.com/anoadragon453/nio-template
# matrix-nio library: https://github.com/poljar/matrix-nio

############ CLEAN SETUP  ####################

# Read the SETUP.md. 
# Setup for native mode (Could be setup in docker, did not try)
# Installing libolm: apt install libolm-dev
# Use default SQLite storage backend (postgres optional, may be used in future).

# Create a bot user:
# We will be using the @test.synapse.local user with pass: pass


######### CHANGING BOT USER MESSAGE THROTTLING SETTINGS ########
# In order to allow the bot to respond to messages quickly,
# we must overwrite the user message throttling settings,
# We will be using the synapse Admin API to make a POST request to the server

## Source ##
https://matrix-org.github.io/synapse/latest/usage/administration/admin_api/

## Getting the admin api key ###
1. Create a matrix user with admin privileges
2. Log in with the user
3. Go to 'All settings' -> 'Help & About' -> 'Advanced' -> 'Access Token' (at the bottom)
4. Copy the Access Token.
# This token is only valid for the duration you are logged in with the user
 
## Making the API call ##
# The call for overwriting the @test:synapse.local throttle settings is:
curl --header "Authorization: Bearer ENTERADMINAPIKEYHERE" -H "Content-Type: application/json" --request POST -k http://localhost:8008/_synapse/admin/v1/users/@test:synapse.local/override_ratelimit
# Should return result of {"messages_per_second":0, "burst_count":0}
#################################


########### Step by step instructions ############

### Installing Prerequisites ###

# Install libolm:
sudo apt install libolm-dev

# Install postgres development headers:
sudo apt install libpq-dev libpq5

# Create a python3 virtual environment in the project location (creates folder 'env'):
virtualenv -p python3 env
# Activate the venv
source env/bin/activate

# Install python dependencies:
pip install -e.

# (Optional) install postgres python dependencies:
pip install -e ".[postgres]"


######## Config file ##########

# Copy sample config file to new 'config.yaml' file
cp sample.config.yaml config.yaml

# config.yaml file edits:
#----------------------------------------------------------
user_id: "@test:synapse.local"
user_password: "pass"

homeserver_url: http://localhost:8080

# Haven't figured out this part yet
device_id: PUTRANDOMCHARSHERE
device_name: test_matrix_bot

# (Optional) change logging levels to debug
level: DEBUG
#----------------------------------------------------------

# Run the bot:
my-project-name

# Invite the bot to a group channel
# Give the bot moderator power level (50)

# In order to allow the bot to mute people:
# Change Roles&Permissions: allow 'Change permissions' to Moderator 

# In order to allow the bot to remove messages:
# allow 'Remove messages sent by others' to Moderator