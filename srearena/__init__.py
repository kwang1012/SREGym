import logging

local_logger = logging.getLogger('all.srearena')
local_logger.propagate = True
local_logger.setLevel(logging.DEBUG)