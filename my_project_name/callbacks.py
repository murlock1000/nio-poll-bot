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

    async def unknown(self, room: MatrixRoom, event: UnknownEvent) -> None:
        """Callback for when an event with a type that is unknown to matrix-nio is received.
        Currently this is used for reaction events, which are not yet part of a released
        matrix spec (and are thus unknown to nio).

        Args:
            room: The room the reaction was sent in.

            event: The event itself.
        """

        if event.type == "org.matrix.msc3381.poll.start":
          #  logger.debug(f"Event content: {event.source}")
            content = event.source.get("content", {}).get("org.matrix.msc3381.poll.start", {})
            topic = content.get("question",{}).get("body")
            kind = content.get("kind","")
            self.store.create_poll(event.room_id, event.event_id, topic, kind)

            answers = content.get("answers", {})

            for answer in answers:
                answer_hash = answer.get("id","")
                answer = answer.get("org.matrix.msc1767.text","")
                self.store.add_answer(answer, answer_hash, event.room_id, event.event_id)

            message = self.get_formatted_poll_results(event.room_id, event.event_id, kind=kind)

            res = await send_text_to_room(
                self.client,
                room.room_id,
                message,
        #        reply_to_event_id=event.event_id,
            )

            self.store.update_reply_event_id_in_poll(event.room_id, event.event_id, res.event_id)

        elif event.type == "org.matrix.msc3381.poll.response":
            sender = event.source.get("sender", "")
            content = event.source.get("content", {})
            response = content.get("org.matrix.msc3381.poll.response", {}).get("answers", [])[0]
            reference_id = content.get("m.relates_to", {}).get("event_id","")
            self.store.create_or_update_response(response, sender, event.room_id, reference_id)
            reply_event = self.store.get_reply_event(event.room_id, reference_id)
            # Get poll event id to reply to if it exists
            if reply_event:
                reply_event_id = reply_event[4]
                kind = reply_event[3]
                message = self.get_formatted_poll_results(event.room_id, reference_id, kind=kind)
                await send_text_to_room(
                    self.client,
                    room.room_id,
                    message,
                    edit_event_id=reply_event_id,
                )

        elif event.type == "org.matrix.msc3381.poll.end":
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

