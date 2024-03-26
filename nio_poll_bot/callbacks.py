import logging
from datetime import datetime
from multiprocessing import Event
from re import L
import asyncio
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
    EncryptedToDeviceEvent,
    MessageDirection,
    RoomMessagesResponse,
    RoomGetEventResponse,
)

from nio_poll_bot.bot_commands import Command
from nio_poll_bot.chat_functions import make_pill, react_to_event, send_text_to_room
from nio_poll_bot.config import Config
from nio_poll_bot.storage import Storage

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

    def get_formatted_poll_results(self, room_id, reference_id, is_final=False, kind="org.matrix.msc3381.poll.disclosed") -> str:
        if kind == "org.matrix.msc3381.poll.disclosed":
            return self._get_formatted_open_poll_results(room_id, reference_id, is_final)
        else:
            return self._get_formatted_closed_poll_results(room_id, reference_id, is_final)

    def _get_formatted_closed_poll_results(self, room_id, reference_id, is_final=False) -> str:
        poll = self.store.get_poll(room_id, reference_id)
        responses = self.store.get_responses(room_id, reference_id)
    
        user_responses = {response[1]:response[0] for response in responses}
        data = ""
        for user in user_responses.keys():
            data += f"{make_pill(user)}\n"
        data += "\n"
        if is_final:
            return f"Final voters for poll `{poll[2]}`:\n\n{data}"
        else:
            return f"Voters for poll `{poll[2]}`:\n\n{data}"

    def _get_formatted_open_poll_results(self, room_id, reference_id, is_final=False) -> str:
        poll = self.store.get_poll(room_id, reference_id)
        responses = self.store.get_responses(room_id, reference_id)
        answers = self.store.get_answers(room_id, reference_id)
        # Send a message acknowledging the reaction
        answer_table = {answer[1]:answer[0] for answer in answers}
        user_responses = {response[1]:response[0] for response in responses}
        
        voters = {answer[1]:[] for answer in answers}
        data = ""
        
        for user, response_hash in user_responses.items():
            voters[response_hash].append(f"{user}")
        for answer_hash, answer in reversed(answer_table.items()):
            data += f"{answer}:\n"
            for user in voters[answer_hash]:
                data += f"{make_pill(user)}\n"
            data += "\n"
        
        if is_final:
            return f"Final poll results for `{poll[2]}`:\n\n{data}"
        else:
            return f"Poll results for `{poll[2]}`:\n\n{data}"
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
        # Send out key request for encrypted events in room so we can accept the m.forwarded_room_key event
        response = await self.client.sync()
        for joined_room_id, room_info in response.rooms.join.items():
            if joined_room_id == room.room_id:
                for ev in room_info.timeline.events:
                    if type(ev) is MegolmEvent:
                        try:
                            # Request keys for the event and update the client store
                            if ev in self.client.outgoing_key_requests:
                                logger.debug("popping the session request")
                                self.client.outgoing_key_requests.pop(ev.session_id)
                            room_key_response = await self.client.request_room_key(ev)
                            await self.client.receive_response(room_key_response)
                        except Exception as e:
                            logger.info(f"Error requesting key for event: {e}")

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

    async def unknown(self, room: MatrixRoom, event: UnknownEvent, ignore_old = True) -> None:
        """Callback for when an event with a type that is unknown to matrix-nio is received.
        Currently this is used for reaction events, which are not yet part of a released
        matrix spec (and are thus unknown to nio).

        Args:
            room: The room the reaction was sent in.

            event: The event itself.
        """
        # If we are not filtering old messages, ignore messages older than 5 minutes
        if not self.config.filter_old_messages and ignore_old:
            if (
                datetime.now() - datetime.fromtimestamp(event.server_timestamp / 1000.0)
            ).total_seconds() > 300:
                return

        if event.type == "org.matrix.msc3381.poll.start" or event.type == "m.poll.start":
          #  logger.debug(f"Event content: {event.source}")
            wrapped_content = event.source.get("content", {})
            if wrapped_content == {}:
                logger.warning("Got poll without content")
                return
            content = wrapped_content.get("org.matrix.msc3381.poll.start", {})
            # Alternate IOS wrapping
            if content == {}:
                content = wrapped_content.get("m.new_content",{}).get("org.matrix.msc3381.poll.end", {})
            if content == {}:
                logger.warning("Got poll without content")
                return
            topic = content.get("question",{}).get("org.matrix.msc1767.text", "")
            if topic == "":
                logger.warning("Got poll without topic")
                return
            kind = content.get("kind","")
            if kind == "":
                logger.warning("Got poll without kind")
                return
            self.store.create_poll(event.room_id, event.event_id, topic, kind)

            answers = content.get("answers", {})

            for answer in answers:
                answer_hash = answer.get("id","")
                answer = answer.get("org.matrix.msc1767.text","")
                if answer_hash == "":
                    logger.warning("Got poll without answer hash")
                    continue
                if answer == "":
                    logger.warning("Got poll without answer")
                    continue
                self.store.add_answer(answer, answer_hash, event.room_id, event.event_id)

            message = self.get_formatted_poll_results(event.room_id, event.event_id, kind=kind)

            res = await send_text_to_room(
                self.client,
                room.room_id,
                message,
        #        reply_to_event_id=event.event_id,
            )

            self.store.update_reply_event_id_in_poll(event.room_id, event.event_id, res.event_id)
        elif event.type == "org.matrix.msc3381.poll.response" or event.type == "m.poll.response":
            sender = event.source.get("sender", "")
            content = event.source.get("content", {})
            response = content.get("org.matrix.msc3381.poll.response", {}).get("answers", [])[0]
            reference_id = content.get("m.relates_to", {}).get("event_id","")
            if reference_id == "":
                logger.warning("Got poll response without reference id")
                return
            reply_event = self.store.get_reply_event(event.room_id, reference_id)
            # Get poll event id to reply to if it exists
            if reply_event:
                self.store.create_or_update_response(response, sender, event.room_id, reference_id)
                reply_event_id = reply_event[4]
                kind = reply_event[3]
                message = self.get_formatted_poll_results(event.room_id, reference_id, kind=kind)
                await send_text_to_room(
                    self.client,
                    room.room_id,
                    message,
                    edit_event_id=reply_event_id,
                )

        elif event.type == "org.matrix.msc3381.poll.end" or event.type == "m.poll.end":
            content = event.source.get("content", {})
            reference_id = content.get("m.relates_to", {}).get("event_id","")
            
            reply_event = self.store.get_reply_event(event.room_id, reference_id)

            if reply_event:
                reply_event_id = reply_event[4]
                kind = reply_event[3]
                message = self.get_formatted_poll_results(event.room_id, reference_id, is_final=True, kind=kind)
                await send_text_to_room(
                    self.client,
                    room.room_id,
                    message,
                    edit_event_id=reply_event_id,
                )
                self.store.delete_poll(event.room_id, reference_id)
            
        else:

            logger.debug(
                f"Got unknown event with type to {event.type} from {event.sender} in {room.room_id}."
            )
            #logger.debug(f"Event content: {event.source}")

    def event_related_to_poll(self, event: Event, event_id: str) -> bool:
        """Check if an event is related to a poll with event_id.
        """
        if type(event) is not UnknownEvent:
            return False
        if event.type == "org.matrix.msc3381.poll.start" or event.type == "m.poll.start":
            return event.event_id == event_id
        elif event.type in ["org.matrix.msc3381.poll.response", "org.matrix.msc3381.poll.end", "m.poll.response", "m.poll.end"]:
            content = event.source.get("content", {})
            reference_id = content.get("m.relates_to", {}).get("event_id","")
            return reference_id == event_id
        else:
            return False

    async def send_warning_message(self, room_id, message, reply_event=None):
        if reply_event:
            await send_text_to_room(self.client, room_id, message, reply_to_event_id=reply_event)
        else:
            await send_text_to_room(self.client, room_id, message)

    async def message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        """Callback for when a message event is received
        Args:
            room: The room the event came from.
            event: The event defining the message.
        """

        # Ignore messages from ourselves
        if event.sender == self.client.user:
            return

        # If we are not filtering old messages, ignore messages older than 5 minutes
        if not self.config.filter_old_messages:
            if (
                datetime.now() - datetime.fromtimestamp(event.server_timestamp / 1000.0)
            ).total_seconds() > 300:
                return

        #logger.debug(f"Event content: {event.source}")
        room_id = event.source.get("room_id", "")
        event_id = event.source.get("content", {}).get("m.relates_to", {}).get("m.in_reply_to", {}).get("event_id", "")
        event_body = event.source.get("content", {}).get("formatted_body", "")

        #logger.debug(f"Got message from {event.sender} in {room_id} with body {event_body}")

        # Check if the message contains pill with bot id
        my_pill = make_pill(self.client.user).split('>')[0]
        if my_pill != event_body[:len(my_pill)]:
            return

        #Check if reply_to is a valid event_id
        if event_id == "":
            return

        # Get the replied to event by event_id
        resp = await self.client.room_get_event(room_id, event_id)
        if type(resp) is not RoomGetEventResponse:
            logger.error(f"{resp}")
            return
        poll_event = resp.event
        # Check if we were unable to decrypt the message
        if type(poll_event) is MegolmEvent:
            logger.info("Got megolm event - trying to get keys")
            try:
                # Request keys for the event and update the client store
                if poll_event.session_id in self.client.outgoing_key_requests:
                    logger.debug("popping the session request")
                    self.client.outgoing_key_requests.pop(poll_event.session_id)
                room_key_response = await self.client.request_room_key(poll_event)
                await self.client.receive_response(room_key_response)
            except Exception as e:
                logger.info(f"Error requesting key for event: {e}")
                await self.send_warning_message(room.room_id, "Unable to get keys for this poll. Please create a new one", event_id)
                return
            try:
                # Check if we are able to decrypt the event now
                poll_event = self.client.olm.decrypt_megolm_event(poll_event, room_id)#self.client.decrypt_event(poll_event)
            except Exception as e:
                logger.info(f"Error decrypting: {e}")
                await self.send_warning_message(room.room_id, "Unable to decrypt this poll. Please create a new one")
                return

        if type(poll_event) is not UnknownEvent:
            logger.info(f"The referenced event is not of UnknownEvent type, instead: {poll_event.type}")
            await self.send_warning_message(room.room_id, f"This is not a poll, but a {poll_event.type}, not unknown", event_id)
            return
        
        # Check if inner event type is a poll.start
        if poll_event.type != "org.matrix.msc3381.poll.start" and poll_event.type != "m.poll.start":
            await self.send_warning_message(room.room_id, f"This is not a poll, but a {poll_event.type}", event_id)
            return

        # Check if the poll already exists in store
        reply_event = self.store.get_reply_event(event.room_id, poll_event.event_id)
        if reply_event:
            logger.info("Poll already replied to")
            await self.send_warning_message(room.room_id, f"This statistics for this poll has already been created.", reply_event[4])
            return

        # Go over all events in the room (break if we find the replied to event) and store the events related to the poll
        event_history = []
        resp = RoomMessagesResponse
        resp.end = self.client.loaded_sync_token
        resp.start = ""
        poll_start_found = False
        while(resp.start != resp.end and not poll_start_found):
            resp = await self.client.room_messages(room_id, resp.end)
            for ev in resp.chunk:
                if self.event_related_to_poll(ev, poll_event.event_id):
                    event_history.append(ev)
                    if ev.type == "org.matrix.msc3381.poll.start" or ev.type == "m.poll.start":
                        poll_start_found = True
                        break

        event_history.reverse()
        # React to all poll events
        for ev in event_history:
            await self.unknown(room, ev, ignore_old = False)
        return 
