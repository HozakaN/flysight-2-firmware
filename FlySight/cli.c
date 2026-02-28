/***************************************************************************
**                                                                        **
**  FlySight 2 firmware                                                   **
**  Copyright 2024 Bionic Avionics Inc.                                   **
**                                                                        **
**  This program is free software: you can redistribute it and/or modify  **
**  it under the terms of the GNU General Public License as published by  **
**  the Free Software Foundation, either version 3 of the License, or     **
**  (at your option) any later version.                                   **
**                                                                        **
**  This program is distributed in the hope that it will be useful,       **
**  but WITHOUT ANY WARRANTY; without even the implied warranty of        **
**  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         **
**  GNU General Public License for more details.                          **
**                                                                        **
**  You should have received a copy of the GNU General Public License     **
**  along with this program.  If not, see <http://www.gnu.org/licenses/>. **
**                                                                        **
****************************************************************************
**  Contact: Bionic Avionics Inc.                                         **
**  Website: http://flysight.ca/                                          **
****************************************************************************/

#include <string.h>
#include <stdio.h>

#include "cli.h"
#include "app_ble.h"
#include "app_common.h"
#include "mode.h"
#include "state.h"
#include "stm32_seq.h"
#include "usbd_cdc_if.h"
#include "version.h"

#define CLI_RX_BUF_SIZE   256
#define CLI_CMD_MAX_LEN   128
#define CLI_TX_BUF_SIZE   256

/* Ring buffer for USB -> CLI data */
static uint8_t rx_buf[CLI_RX_BUF_SIZE];
static volatile uint16_t rx_head;
static volatile uint16_t rx_tail;

/* Command line accumulator */
static char cmd_buf[CLI_CMD_MAX_LEN];
static uint16_t cmd_len;

/* Transmit buffer */
static char tx_buf[CLI_TX_BUF_SIZE];

static void FS_CLI_ProcessTask(void);
static void FS_CLI_Send(const char *str);
static void FS_CLI_Execute(const char *cmd);

void FS_CLI_Init(void)
{
	rx_head = 0;
	rx_tail = 0;
	cmd_len = 0;

	UTIL_SEQ_RegTask(1 << CFG_TASK_FS_CLI_UPDATE_ID, UTIL_SEQ_RFU, FS_CLI_ProcessTask);

	FS_CLI_Send("\r\nFlySight CLI ready. Type 'help' for commands.\r\n> ");
}

void FS_CLI_DeInit(void)
{
	rx_head = 0;
	rx_tail = 0;
	cmd_len = 0;
}

void FS_CLI_RxCallback(const uint8_t *data, uint32_t len)
{
	for (uint32_t i = 0; i < len; i++)
	{
		uint16_t next = (rx_head + 1) % CLI_RX_BUF_SIZE;
		if (next != rx_tail)
		{
			rx_buf[rx_head] = data[i];
			rx_head = next;
		}
	}

	UTIL_SEQ_SetTask(1 << CFG_TASK_FS_CLI_UPDATE_ID, CFG_SCH_PRIO_1);
}

static void FS_CLI_Send(const char *str)
{
	uint16_t len = (uint16_t)strlen(str);
	if (len > 0)
	{
		/* CDC_Transmit_FS may return BUSY if a previous transmission
		 * is still in progress. For a simple CLI this is acceptable —
		 * worst case some output is dropped. */
		CDC_Transmit_FS((uint8_t *)str, len);
	}
}

static void FS_CLI_ProcessTask(void)
{
	while (rx_head != rx_tail)
	{
		uint8_t ch = rx_buf[rx_tail];
		rx_tail = (rx_tail + 1) % CLI_RX_BUF_SIZE;

		if (ch == '\r' || ch == '\n')
		{
			if (cmd_len > 0)
			{
				cmd_buf[cmd_len] = '\0';
				FS_CLI_Send("\r\n");
				FS_CLI_Execute(cmd_buf);
				cmd_len = 0;
				FS_CLI_Send("> ");
			}
		}
		else if (ch == 0x7F || ch == '\b')
		{
			if (cmd_len > 0)
			{
				cmd_len--;
				FS_CLI_Send("\b \b");
			}
		}
		else if (ch >= 0x20 && cmd_len < CLI_CMD_MAX_LEN - 1)
		{
			cmd_buf[cmd_len++] = (char)ch;
			/* Echo character back */
			char echo[2] = {(char)ch, '\0'};
			FS_CLI_Send(echo);
		}
	}
}

static void FS_CLI_Execute(const char *cmd)
{
	if (strcmp(cmd, "help") == 0)
	{
		FS_CLI_Send(
			"Available commands:\r\n"
			"  pair start  - Start BLE pairing mode\r\n"
			"  pair stop   - Stop BLE pairing mode\r\n"
			"  status      - Show device mode and BLE state\r\n"
			"  version     - Show firmware version\r\n"
			"  help        - Show this help\r\n"
		);
	}
	else if (strcmp(cmd, "pair start") == 0)
	{
		if (!FS_State_Get()->enable_ble)
		{
			FS_CLI_Send("ERROR: BLE is disabled in device settings.\r\n");
			return;
		}
		FS_CLI_Send("Starting BLE pairing...\r\n");
		APP_BLE_RequestPairing(NULL);
	}
	else if (strcmp(cmd, "pair stop") == 0)
	{
		FS_CLI_Send("Stopping BLE pairing.\r\n");
		APP_BLE_CancelPairing();
	}
	else if (strcmp(cmd, "status") == 0)
	{
		static const char *mode_names[] = {
			"SLEEP", "ACTIVE", "CONFIG", "USB", "PAIRING", "START"
		};
		static const char *ble_state_names[] = {
			"IDLE", "FAST_ADV", "LP_ADV", "SCAN", "CONNECTING", "CONNECTED"
		};

		FS_Mode_State_t mode = FS_Mode_State();
		APP_BLE_ConnStatus_t ble_st = APP_BLE_Get_Server_Connection_Status();

		snprintf(tx_buf, sizeof(tx_buf),
			"Mode: %s\r\n"
			"BLE: %s (%s)\r\n",
			(mode < FS_MODE_STATE_COUNT) ? mode_names[mode] : "UNKNOWN",
			FS_State_Get()->enable_ble ? "enabled" : "disabled",
			ble_state_names[ble_st]);
		FS_CLI_Send(tx_buf);
	}
	else if (strcmp(cmd, "version") == 0)
	{
		snprintf(tx_buf, sizeof(tx_buf), "FlySight FW: %s\r\n", GIT_TAG);
		FS_CLI_Send(tx_buf);
	}
	else if (cmd[0] != '\0')
	{
		FS_CLI_Send("Unknown command. Type 'help' for available commands.\r\n");
	}
}
