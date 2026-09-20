<?php

final class GuildController
{
    public function inviteGuild(int $guildId): string
    {
        $limit = Config::get('anchor.joinBDGuildDayLimit');
        return $limit > 3 ? 'BD_OP_FORBIDDEN' : 'OK';
    }
}
