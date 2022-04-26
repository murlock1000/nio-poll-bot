import logging
from typing import Dict, List, Optional, Tuple, Union

from aiohttp import ClientResponse
from markdown import markdown
from nio import (
    AsyncClient,
    ErrorResponse,
    MatrixRoom,
    MegolmEvent,
    Response,
    RoomCreateError,
    RoomGetStateEventError,
    RoomGetStateEventResponse,
    RoomPreset,
    RoomPutStateError,
    RoomPutStateResponse,
    RoomSendResponse,
    RoomVisibility,
    SendRetryError,
)
from nio.http import TransportResponse

logger = logging.getLogger(__name__)


async def send_text_to_room(
    client: AsyncClient,
    room_id: str,
    message: str,
    notice: bool = True,
    markdown_convert: bool = True,
    reply_to_event_id: Optional[str] = None,
) -> Union[RoomSendResponse, ErrorResponse]:
    """Send text to a matrix room.

    Args:
        client: The client to communicate to matrix with.

        room_id: The ID of the room to send the message to.

        message: The message content.

        notice: Whether the message should be sent with an "m.notice" message type
            (will not ping users).

        markdown_convert: Whether to convert the message content to markdown.
            Defaults to true.

        reply_to_event_id: Whether this message is a reply to another event. The event
            ID this is message is a reply to.

    Returns:
        A RoomSendResponse if the request was successful, else an ErrorResponse.
    """
    # Determine whether to ping room members or not
    msgtype = "m.notice" if notice else "m.text"

    content = {
        "msgtype": msgtype,
        "format": "org.matrix.custom.html",
        "body": message,
    }

    if markdown_convert:
        content["formatted_body"] = markdown(message)

    if reply_to_event_id:
        content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}

    try:
        return await client.room_send(
            room_id,
            "m.room.message",
            content,
            ignore_unverified_devices=True,
        )
    except SendRetryError:
        logger.exception(f"Unable to send message response to {room_id}")


def make_pill(user_id: str, displayname: str = None) -> str:
    """Convert a user ID (and optionally a display name) to a formatted user 'pill'

    Args:
        user_id: The MXID of the user.

        displayname: An optional displayname. Clients like Element will figure out the
            correct display name no matter what, but other clients may not. If not
            provided, the MXID will be used instead.

    Returns:
        The formatted user pill.
    """
    if not displayname:
        # Use the user ID as the displayname if not provided
        displayname = user_id

    return f'<a href="https://matrix.to/#/{user_id}">{displayname}</a>'


async def react_to_event(
    client: AsyncClient,
    room_id: str,
    event_id: str,
    reaction_text: str,
) -> Union[Response, ErrorResponse]:
    """Reacts to a given event in a room with the given reaction text

    Args:
        client: The client to communicate to matrix with.

        room_id: The ID of the room to send the message to.

        event_id: The ID of the event to react to.

        reaction_text: The string to react with. Can also be (one or more) emoji characters.

    Returns:
        A nio.Response or nio.ErrorResponse if an error occurred.

    Raises:
        SendRetryError: If the reaction was unable to be sent.
    """
    content = {
        "m.relates_to": {
            "rel_type": "m.annotation",
            "event_id": event_id,
            "key": reaction_text,
        }
    }

    return await client.room_send(
        room_id,
        "m.reaction",
        content,
        ignore_unverified_devices=True,
    )


async def decryption_failure(self, room: MatrixRoom, event: MegolmEvent) -> None:
    """Callback for when an event fails to decrypt. Inform the user"""
    logger.error(
        f"Failed to decrypt event '{event.event_id}' in room '{room.room_id}'!"
        f"\n\n"
        f"Tip: try using a different device ID in your config file and restart."
        f"\n\n"
        f"If all else fails, delete your store directory and let the bot recreate "
        f"it (your reminders will NOT be deleted, but the bot may respond to existing "
        f"commands a second time)."
    )

    user_msg = (
        "Unable to decrypt this message. "
        "Check whether you've chosen to only encrypt to trusted devices."
    )

    await send_text_to_room(
        self.client,
        room.room_id,
        user_msg,
        reply_to_event_id=event.event_id,
    )


async def send_msg(client: AsyncClient, mxid: str, roomname: str, message: str):
    """
    :param mxid: A Matrix user id to send the message to
    :param roomname: A Matrix room id to send the message to
    :param message: Text to be sent as message
    :return bool: Success upon sending the message
    """
    # Sends private message to user. Returns true on success.
    msg_room = await find_or_create_private_msg(client, mxid, roomname)
    if not msg_room or (type(msg_room) is RoomCreateError):
        logger.error(f'Unable to create room when trying to message {mxid}')
        return False
    # Send message to the room
    await send_text_to_room(client, msg_room.room_id, message)
    return True


async def find_or_create_private_msg(client: AsyncClient, mxid: str, roomname: str):
    # Find if we already have a common room with user:
    msg_room = None
    for croomid in client.rooms:
        roomobj = client.rooms[croomid]
        if len(roomobj.users) == 2:
            for user in roomobj.users:
                if user == mxid:
                    msg_room = roomobj
    # Nope, let's create one
    if not msg_room:
        msg_room = await client.room_create(visibility=RoomVisibility.private,
                                            name=roomname,
                                            is_direct=True,
                                            preset=RoomPreset.private_chat,
                                            invite={mxid},
                                            )
    return msg_room


# Code for changing user power was taken from https://github.com/elokapina/bubo/commit/d2a69117e52bb15090f993f79eeed8dbc3b3e4ae
async def with_ratelimit(client: AsyncClient, method: str, *args, **kwargs):
    func = getattr(client, method)
    response = await func(*args, **kwargs)
    if getattr(response, "status_code", None) == "M_LIMIT_EXCEEDED":
        return with_ratelimit(client, method, *args, **kwargs)
    return response


async def set_user_power(
    room_id: str, user_id: str, client: AsyncClient, power: int,
) -> Union[int, RoomGetStateEventError, RoomGetStateEventResponse, RoomPutStateError, RoomPutStateResponse]:
    """
    Set user power in a room.
    """
    logger.debug(f"Setting user power: {room_id}, user: {user_id}, level: {power}")
    state_response = await client.room_get_state_event(room_id, "m.room.power_levels")
    if isinstance(state_response, RoomGetStateEventError):
        logger.error(f"Failed to fetch room {room_id} state: {state_response.message}")
        return state_response
    if isinstance(state_response.transport_response, TransportResponse):
        status_code = state_response.transport_response.status_code
    elif isinstance(state_response.transport_response, ClientResponse):
        status_code = state_response.transport_response.status
    else:
        logger.error(f"Failed to determine status code from state response: {state_response}")
        return state_response
    if status_code >= 400:
        logger.warning(
            f"Failed to set user {user_id} power in {room_id}, response {status_code}"
        )
        return status_code
    state_response.content["users"][user_id] = power
    response = await with_ratelimit(
        client,
        "room_put_state",
        room_id=room_id,
        event_type="m.room.power_levels",
        content=state_response.content,
    )
    return response
