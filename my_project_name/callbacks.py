import logging
from datetime import datetime
from nio import (
    AsyncClient,
    InviteMemberEvent,
    JoinError,
    MatrixRoom,
    MegolmEvent,
    RoomGetEventError,
    RoomMessageText,
    UnknownEvent,
    RoomMemberEvent,
)

from my_project_name.bot_commands import Command
from my_project_name.chat_functions import make_pill, react_to_event, send_text_to_room
from my_project_name.config import Config
from my_project_name.storage import Storage

logger = logging.getLogger(__name__)


class Callbacks:
    def __init__(self, client: AsyncClient, store: Storage, config: Config):
        """
        Args:
            client: nio client used to interact with matrix.

            store: Bot storage.

            config: Bot configuration parameters.
        """
        self.client = client
        self.store = store
        self.config = config
        self.command_prefix = config.command_prefix

    def _check_if_message_from_thread(self, event: RoomMessageText):
        """Extracts the rel_type from a RoomMessageText object content

        Args:
            event: The event defining the message.
        """
        event_content = event.source["content"]
        # relates_to contains data about the replied to message
        relates_to = event_content.get("m.relates_to")

        is_thread_reply = False
        if relates_to is not None:
            is_thread_reply = relates_to.get("rel_type", False) == "m.thread"

        return is_thread_reply

    async def message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        """Callback for when a message event is received

        Args:
            room: The room the event came from.

            event: The event defining the message.
        """
        # Extract the message text
        msg = event.body

        # Extract flag if the message is in a thread
        is_thread_reply = self._check_if_message_from_thread(event)

        # Ignore messages from ourselves
        if event.sender == self.client.user:
            return

        # If we are not filtering old messages, ignore messages older than 5 minutes
        if not self.config.filter_old_messages:
            if (
                datetime.now() - datetime.fromtimestamp(event.server_timestamp / 1000.0)
            ).total_seconds() > 300:
                return

        logger.debug(
            f"Bot message received for room {room.display_name} | "
            f"{room.user_name(event.sender)}: {msg}"
        )

        # Process as message if in a public room without command prefix
        # has_command_prefix = msg.startswith(self.command_prefix)

        # room.is_group is often a DM, but not always.
        # room.is_group does not allow room aliases
        # room.member_count > 2 ... we assume a public room
        # room.member_count <= 2 ... we assume a DM
        if room.member_count > 2 and not is_thread_reply:
            # Call the filter method on a message in a channel that not a thread discussion and does not contain the prefix (! REMOVE PREFIXES ENTIRELLY !)
            command = Command(self.client, self.store, self.config, msg, room, event)
            await command.filter_channel()
            return

    async def invite(self, room: MatrixRoom, event: InviteMemberEvent) -> None:
        """Callback for when an invite is received. Join the room specified in the invite.

        Args:
            room: The room that we are invited to.

            event: The invite event.
        """
        logger.debug(f"Got invite to {room.room_id} from {event.sender}.")

        # Attempt to join 3 times before giving up
        for attempt in range(3):
            result = await self.client.join(room.room_id)
            if type(result) == JoinError:
                logger.error(
                    f"Error joining room {room.room_id} (attempt %d): %s",
                    attempt,
                    result.message,
                )
            else:
                break
        else:
            logger.error("Unable to join room: %s", room.room_id)

        # Successfully joined room
        logger.info(f"Joined {room.room_id}")

    async def invite_event_filtered_callback(
        self, room: MatrixRoom, event: InviteMemberEvent
    ) -> None:
        """
        Since the InviteMemberEvent is fired for every m.room.member state received
        in a sync response's `rooms.invite` section, we will receive some that are
        not actually our own invite event (such as the inviter's membership).
        This makes sure we only call `callbacks.invite` with our own invite events.
        """
        if event.state_key == self.client.user_id:
            # This is our own membership (invite) event
            await self.invite(room, event)

    async def _reaction(
        self, room: MatrixRoom, event: UnknownEvent, reacted_to_id: str
    ) -> None:
        """A reaction was sent to one of our messages. Let's send a reply acknowledging it.

        Args:
            room: The room the reaction was sent in.

            event: The reaction event.

            reacted_to_id: The event ID that the reaction points to.
        """
        logger.debug(f"Got reaction to {room.room_id} from {event.sender}.")

        # Get the original event that was reacted to
        event_response = await self.client.room_get_event(room.room_id, reacted_to_id)
        if isinstance(event_response, RoomGetEventError):
            logger.warning(
                "Error getting event that was reacted to (%s)", reacted_to_id
            )
            return
        reacted_to_event = event_response.event

        # Only acknowledge reactions to events that we sent
        if reacted_to_event.sender != self.config.user_id:
            return

        # Send a message acknowledging the reaction
        reaction_sender_pill = make_pill(event.sender)
        reaction_content = (
            event.source.get("content", {}).get("m.relates_to", {}).get("key")
        )
        message = (
            f"{reaction_sender_pill} reacted to this event with `{reaction_content}`!"
        )
        await send_text_to_room(
            self.client,
            room.room_id,
            message,
            reply_to_event_id=reacted_to_id,
        )

    async def decryption_failure(self, room: MatrixRoom, event: MegolmEvent) -> None:
        """Callback for when an event fails to decrypt. Inform the user.

        Args:
            room: The room that the event that we were unable to decrypt is in.

            event: The encrypted event that we were unable to decrypt.
        """
        logger.error(
            f"Failed to decrypt event '{event.event_id}' in room '{room.room_id}'!"
            f"\n\n"
            f"Tip: try using a different device ID in your config file and restart."
            f"\n\n"
            f"If all else fails, delete your store directory and let the bot recreate "
            f"it (your reminders will NOT be deleted, but the bot may respond to existing "
            f"commands a second time)."
        )

        red_x_and_lock_emoji = "❌ 🔐"

        # React to the undecryptable event with some emoji
        await react_to_event(
            self.client,
            room.room_id,
            event.event_id,
            red_x_and_lock_emoji,
        )

    async def joined(self, room: MatrixRoom, event: RoomMemberEvent) -> None:
        """Callback for when a user invites/leaves/joins a room

        Args:
            room: The room the event came from.

            event: The event defining the message.
        """
        # Check if the call happened in a 1-1 room
        event_content = event.source["content"]
        user_joined = event_content["membership"] == "join"

        # Check If a user joined a DM with the bot.
        if room.member_count == 2 and user_joined:
            # command = Command(self.client, self.store, self.config, "", room, event)
            # await command.resend_notification()
            return

    async def unknown(self, room: MatrixRoom, event: UnknownEvent) -> None:
        """Callback for when an event with a type that is unknown to matrix-nio is received.
        Currently this is used for reaction events, which are not yet part of a released
        matrix spec (and are thus unknown to nio).

        Args:
            room: The room the reaction was sent in.

            event: The event itself.
        """
        if event.type == "m.reaction":
            # Get the ID of the event this was a reaction to
            relation_dict = event.source.get("content", {}).get("m.relates_to", {})

            reacted_to = relation_dict.get("event_id")
            if reacted_to and relation_dict.get("rel_type") == "m.annotation":
                await self._reaction(room, event, reacted_to)
                return

        logger.debug(
            f"Got unknown event with type to {event.type} from {event.sender} in {room.room_id}."
        )
