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

#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>

#include "cli.h"
#include "app_ble.h"
#include "app_common.h"
#include "ff.h"
#include "mode.h"
#include "resource_manager.h"
#include "state.h"
#include "stm32_seq.h"
#include "cli_transport.h"
#include "usbd_composite.h"
#include "version.h"

#define CLI_RX_BUF_SIZE   256
#define CLI_CMD_MAX_LEN   128
#define CLI_TX_BUF_SIZE   256
#define CLI_PATH_MAX      64

#define CLI_EOF_CHAR      0x04  /* Ctrl-D */

/* CLI modes */
typedef enum
{
	CLI_MODE_COMMAND,
	CLI_MODE_FILE_WRITE,
	CLI_MODE_BINARY_WRITE
} CLI_Mode_t;

/* Ring buffer for USB -> CLI data */
static uint8_t rx_buf[CLI_RX_BUF_SIZE];
static volatile uint16_t rx_head;
static volatile uint16_t rx_tail;

/* Command line accumulator */
static char cmd_buf[CLI_CMD_MAX_LEN];
static uint16_t cmd_len;

/* Transmit buffer (also used for file chunk reads) */
static char tx_buf[CLI_TX_BUF_SIZE];

/* Path buffer for file operations */
static char path_buf[CLI_PATH_MAX];

/* Active transport for TX routing */
static const FS_CLI_Transport_t *active_transport = NULL;

/* File write state */
static CLI_Mode_t cli_mode;
static FIL write_file;
static uint32_t write_total;
static uint32_t write_expected;

static void FS_CLI_ProcessTask(void);
static void FS_CLI_Send(const char *str);
static void FS_CLI_SendBuf(const char *buf, uint16_t len);
static void FS_CLI_Execute(const char *cmd);

void FS_CLI_Init(void)
{
	rx_head = 0;
	rx_tail = 0;
	cmd_len = 0;
	cli_mode = CLI_MODE_COMMAND;

	UTIL_SEQ_RegTask(1 << CFG_TASK_FS_CLI_UPDATE_ID, UTIL_SEQ_RFU, FS_CLI_ProcessTask);
}

void FS_CLI_DeInit(void)
{
	/* Close any open write file */
	if (cli_mode == CLI_MODE_FILE_WRITE || cli_mode == CLI_MODE_BINARY_WRITE)
	{
		f_close(&write_file);
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
		cli_mode = CLI_MODE_COMMAND;
	}

	rx_head = 0;
	rx_tail = 0;
	cmd_len = 0;
}

void FS_CLI_SetActiveTransport(const FS_CLI_Transport_t *transport)
{
	active_transport = transport;
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
	if (!active_transport) return;

	uint16_t len = (uint16_t)strlen(str);
	if (len > 0)
	{
		uint32_t retry = 0;
		while (active_transport->transmit((const uint8_t *)str, len) != 0)
		{
			if (++retry > 100000U)
				return;
		}
		retry = 0;
		while (active_transport->tx_busy())
		{
			if (++retry > 100000U)
				break;
		}
	}
}

static void FS_CLI_SendBuf(const char *buf, uint16_t len)
{
	if (!active_transport) return;

	if (len > 0)
	{
		uint32_t retry = 0;
		while (active_transport->transmit((const uint8_t *)buf, len) != 0)
		{
			if (++retry > 100000U)
				return;
		}
		retry = 0;
		while (active_transport->tx_busy())
		{
			if (++retry > 100000U)
				break;
		}
	}
}

/*---------------------------------------------------------------------------*/
/* File write mode handler                                                   */
/*---------------------------------------------------------------------------*/

static void FS_CLI_ProcessFileWrite(uint8_t ch)
{
	UINT bw;

	if (ch == CLI_EOF_CHAR)
	{
		/* End of file transfer */
		f_close(&write_file);
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);

		snprintf(tx_buf, sizeof(tx_buf), "OK: Written %lu bytes\r\n", write_total);
		FS_CLI_Send(tx_buf);

		cli_mode = CLI_MODE_COMMAND;
		FS_CLI_Send("> ");
		return;
	}

	/* Write byte to file */
	if (f_write(&write_file, &ch, 1, &bw) == FR_OK)
	{
		write_total += bw;
	}
}

static void FS_CLI_ProcessBinaryWrite(uint8_t ch)
{
	UINT bw;

	if (f_write(&write_file, &ch, 1, &bw) == FR_OK)
	{
		write_total += bw;
	}

	if (write_total >= write_expected)
	{
		f_close(&write_file);
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);

		snprintf(tx_buf, sizeof(tx_buf), "OK: Written %lu bytes\r\n", write_total);
		FS_CLI_Send(tx_buf);

		cli_mode = CLI_MODE_COMMAND;
		FS_CLI_Send("> ");
	}
}

/*---------------------------------------------------------------------------*/
/* Command mode handler                                                      */
/*---------------------------------------------------------------------------*/

static void FS_CLI_ProcessCommand(uint8_t ch)
{
	if (ch == '\r' || ch == '\n')
	{
		if (cmd_len > 0)
		{
			cmd_buf[cmd_len] = '\0';
			FS_CLI_Send("\r\n");
			FS_CLI_Execute(cmd_buf);
			cmd_len = 0;
			if (cli_mode == CLI_MODE_COMMAND)
			{
				FS_CLI_Send("> ");
			}
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

static void FS_CLI_ProcessTask(void)
{
	while (rx_head != rx_tail)
	{
		uint8_t ch = rx_buf[rx_tail];
		rx_tail = (rx_tail + 1) % CLI_RX_BUF_SIZE;

		if (cli_mode == CLI_MODE_FILE_WRITE)
		{
			FS_CLI_ProcessFileWrite(ch);
		}
		else if (cli_mode == CLI_MODE_BINARY_WRITE)
		{
			FS_CLI_ProcessBinaryWrite(ch);
		}
		else
		{
			FS_CLI_ProcessCommand(ch);
		}
	}
}

/*---------------------------------------------------------------------------*/
/* File access helpers                                                       */
/*---------------------------------------------------------------------------*/

static uint8_t FS_CLI_CheckFileAccess(void)
{
	if (USBD_Composite_IsMSCEnabled())
	{
		FS_CLI_Send("ERROR: File access requires Competition_Mode: 1 in flysight.txt\r\n");
		return 0;
	}
	return 1;
}

/* Check if name matches YY-MM-DD or HH-MM-SS pattern */
static uint8_t FS_CLI_IsDateTimeFolder(const char *name)
{
	if (strlen(name) != 8) return 0;
	if (!isdigit((unsigned char)name[0])) return 0;
	if (!isdigit((unsigned char)name[1])) return 0;
	if (name[2] != '-') return 0;
	if (!isdigit((unsigned char)name[3])) return 0;
	if (!isdigit((unsigned char)name[4])) return 0;
	if (name[5] != '-') return 0;
	if (!isdigit((unsigned char)name[6])) return 0;
	if (!isdigit((unsigned char)name[7])) return 0;
	return 1;
}

static void FS_CLI_SendFileContents(const char *path)
{
	static FIL file;
	UINT br;

	if (f_open(&file, path, FA_READ) != FR_OK)
	{
		snprintf(tx_buf, sizeof(tx_buf), "ERROR: Cannot open %s\r\n", path);
		FS_CLI_Send(tx_buf);
		return;
	}

	while (f_read(&file, tx_buf, sizeof(tx_buf) - 1, &br) == FR_OK && br > 0)
	{
		FS_CLI_SendBuf(tx_buf, (uint16_t)br);
	}

	f_close(&file);
}

static void FS_CLI_ReadFile(const char *path)
{
	if (!FS_CLI_CheckFileAccess()) return;

	if (FS_ResourceManager_RequestResource(FS_RESOURCE_FATFS) != FS_RESOURCE_MANAGER_SUCCESS)
	{
		FS_CLI_Send("ERROR: Cannot access filesystem.\r\n");
		return;
	}

	snprintf(tx_buf, sizeof(tx_buf), "=== %s ===\r\n", path);
	FS_CLI_Send(tx_buf);

	FS_CLI_SendFileContents(path);

	FS_CLI_Send("\r\n=== END ===\r\n");

	FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
}

static void FS_CLI_WriteFile(const char *path)
{
	if (!FS_CLI_CheckFileAccess()) return;

	if (FS_ResourceManager_RequestResource(FS_RESOURCE_FATFS) != FS_RESOURCE_MANAGER_SUCCESS)
	{
		FS_CLI_Send("ERROR: Cannot access filesystem.\r\n");
		return;
	}

	if (f_open(&write_file, path, FA_WRITE | FA_CREATE_ALWAYS) != FR_OK)
	{
		snprintf(tx_buf, sizeof(tx_buf), "ERROR: Cannot open %s for writing.\r\n", path);
		FS_CLI_Send(tx_buf);
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
		return;
	}

	write_total = 0;
	cli_mode = CLI_MODE_FILE_WRITE;
	FS_CLI_Send("READY\r\n");
}

static uint8_t FS_CLI_FindLatestTrack(char *out_path, uint16_t max_len)
{
	DIR dir;
	FILINFO fno;
	char best_date[9] = {0};
	char best_time[9] = {0};

	/* Scan root for date folders */
	if (f_opendir(&dir, "/") != FR_OK) return 0;

	while (f_readdir(&dir, &fno) == FR_OK && fno.fname[0] != '\0')
	{
		if ((fno.fattrib & AM_DIR) && FS_CLI_IsDateTimeFolder(fno.fname))
		{
			if (strcmp(fno.fname, best_date) > 0)
			{
				strncpy(best_date, fno.fname, sizeof(best_date) - 1);
			}
		}
	}
	f_closedir(&dir);

	if (best_date[0] == '\0') return 0;

	/* Scan inside the latest date folder for time folders */
	snprintf(path_buf, sizeof(path_buf), "/%s", best_date);
	if (f_opendir(&dir, path_buf) != FR_OK) return 0;

	while (f_readdir(&dir, &fno) == FR_OK && fno.fname[0] != '\0')
	{
		if ((fno.fattrib & AM_DIR) && FS_CLI_IsDateTimeFolder(fno.fname))
		{
			if (strcmp(fno.fname, best_time) > 0)
			{
				strncpy(best_time, fno.fname, sizeof(best_time) - 1);
			}
		}
	}
	f_closedir(&dir);

	if (best_time[0] == '\0') return 0;

	snprintf(out_path, max_len, "/%s/%s", best_date, best_time);
	return 1;
}

/*---------------------------------------------------------------------------*/
/* Command implementations                                                   */
/*---------------------------------------------------------------------------*/

static void FS_CLI_CmdTrackLatest(void)
{
	DIR dir;
	FILINFO fno;
	char track_path[CLI_PATH_MAX];
	char file_path[CLI_PATH_MAX + 14]; /* room for track_path + '/' + 8.3 name */

	if (!FS_CLI_CheckFileAccess()) return;

	if (FS_ResourceManager_RequestResource(FS_RESOURCE_FATFS) != FS_RESOURCE_MANAGER_SUCCESS)
	{
		FS_CLI_Send("ERROR: Cannot access filesystem.\r\n");
		return;
	}

	if (!FS_CLI_FindLatestTrack(track_path, sizeof(track_path)))
	{
		FS_CLI_Send("No track found.\r\n");
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
		return;
	}

	snprintf(tx_buf, sizeof(tx_buf), "Latest track: %s\r\n", track_path);
	FS_CLI_Send(tx_buf);

	/* Open the track directory and send all files */
	if (f_opendir(&dir, track_path) != FR_OK)
	{
		FS_CLI_Send("ERROR: Cannot open track directory.\r\n");
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
		return;
	}

	while (f_readdir(&dir, &fno) == FR_OK && fno.fname[0] != '\0')
	{
		if (fno.fattrib & AM_DIR) continue;  /* Skip subdirectories */

		snprintf(file_path, sizeof(file_path), "%s/%s", track_path, fno.fname);

		/* Skip binary files */
		if (strcmp(fno.fname, "raw.ubx") == 0)
		{
			snprintf(tx_buf, sizeof(tx_buf), "=== %s === (binary, skipped)\r\n", file_path);
			FS_CLI_Send(tx_buf);
			continue;
		}

		snprintf(tx_buf, sizeof(tx_buf), "=== %s ===\r\n", file_path);
		FS_CLI_Send(tx_buf);

		FS_CLI_SendFileContents(file_path);
		FS_CLI_Send("\r\n");
	}

	f_closedir(&dir);

	FS_CLI_Send("=== END ===\r\n");

	FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
}

static void FS_CLI_CmdMSC(void)
{
	FS_State_SetCompetitionMode(0);
	FS_State_Save();
	FS_CLI_Send("Competition mode disabled. Unplug and replug USB for mass storage.\r\n");
}

static FRESULT FS_CLI_DeleteNode(TCHAR *path, UINT sz_buff, FILINFO *fno)
{
	UINT i, j;
	FRESULT fr;
	DIR dir;

	fr = f_opendir(&dir, path);
	if (fr != FR_OK) return fr;

	for (i = 0; path[i]; i++) ;
	path[i++] = '/';

	for (;;)
	{
		fr = f_readdir(&dir, fno);
		if (fr != FR_OK || !fno->fname[0]) break;
		j = 0;
		do
		{
			if (i + j >= sz_buff) { fr = (FRESULT)100; break; }
			path[i + j] = fno->fname[j];
		} while (fno->fname[j++]);
		if (fno->fattrib & AM_DIR)
			fr = FS_CLI_DeleteNode(path, sz_buff, fno);
		else
			fr = f_unlink(path);
		if (fr != FR_OK) break;
	}

	path[--i] = 0;
	f_closedir(&dir);

	if (fr == FR_OK) fr = f_unlink(path);
	return fr;
}

static void FS_CLI_CmdTempList(void)
{
	DIR dir, subdir;
	FILINFO fno;

	if (!FS_CLI_CheckFileAccess()) return;

	if (FS_ResourceManager_RequestResource(FS_RESOURCE_FATFS) != FS_RESOURCE_MANAGER_SUCCESS)
	{
		FS_CLI_Send("ERROR: Cannot access filesystem.\r\n");
		return;
	}

	if (f_opendir(&dir, "/temp") != FR_OK)
	{
		FS_CLI_Send("No /temp folder found.\r\n");
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
		return;
	}

	uint16_t count = 0;
	while (f_readdir(&dir, &fno) == FR_OK && fno.fname[0] != '\0')
	{
		if (fno.fattrib & AM_DIR)
		{
			snprintf(tx_buf, sizeof(tx_buf), "%s/\r\n", fno.fname);
			FS_CLI_Send(tx_buf);

			snprintf(path_buf, sizeof(path_buf), "/temp/%s", fno.fname);
			if (f_opendir(&subdir, path_buf) == FR_OK)
			{
				FILINFO sfno;
				while (f_readdir(&subdir, &sfno) == FR_OK && sfno.fname[0] != '\0')
				{
					snprintf(tx_buf, sizeof(tx_buf), "  %s (%lu bytes)\r\n",
						sfno.fname, (unsigned long)sfno.fsize);
					FS_CLI_Send(tx_buf);
				}
				f_closedir(&subdir);
			}
		}
		else
		{
			snprintf(tx_buf, sizeof(tx_buf), "%s (%lu bytes)\r\n",
				fno.fname, (unsigned long)fno.fsize);
			FS_CLI_Send(tx_buf);
		}
		count++;
	}

	f_closedir(&dir);

	if (count == 0)
	{
		FS_CLI_Send("(empty)\r\n");
	}

	FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
}

static void FS_CLI_CmdTempDelete(void)
{
	DIR dir;
	FILINFO fno;
	TCHAR del_path[CLI_PATH_MAX];

	if (!FS_CLI_CheckFileAccess()) return;

	if (FS_ResourceManager_RequestResource(FS_RESOURCE_FATFS) != FS_RESOURCE_MANAGER_SUCCESS)
	{
		FS_CLI_Send("ERROR: Cannot access filesystem.\r\n");
		return;
	}

	if (f_opendir(&dir, "/temp") != FR_OK)
	{
		FS_CLI_Send("No /temp folder found.\r\n");
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
		return;
	}

	uint16_t count = 0;
	FRESULT fr = FR_OK;

	while (f_readdir(&dir, &fno) == FR_OK && fno.fname[0] != '\0')
	{
		snprintf(del_path, sizeof(del_path), "/temp/%s", fno.fname);

		if (fno.fattrib & AM_DIR)
			fr = FS_CLI_DeleteNode(del_path, sizeof(del_path), &fno);
		else
			fr = f_unlink(del_path);

		if (fr != FR_OK)
		{
			snprintf(tx_buf, sizeof(tx_buf), "ERROR: Failed to delete %s\r\n", del_path);
			FS_CLI_Send(tx_buf);
			break;
		}
		count++;
	}

	f_closedir(&dir);

	if (fr == FR_OK)
	{
		snprintf(tx_buf, sizeof(tx_buf), "Deleted %u item(s) from /temp\r\n", count);
		FS_CLI_Send(tx_buf);
	}

	FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
}

static void FS_CLI_CmdFwUpload(uint32_t size)
{
	if (!FS_CLI_CheckFileAccess()) return;

	if (FS_ResourceManager_RequestResource(FS_RESOURCE_FATFS) != FS_RESOURCE_MANAGER_SUCCESS)
	{
		FS_CLI_Send("ERROR: Cannot access filesystem.\r\n");
		return;
	}

	f_mkdir("/FW");  /* ignore error if already exists */

	if (f_open(&write_file, "/FW/app.sfb", FA_WRITE | FA_CREATE_ALWAYS) != FR_OK)
	{
		FS_CLI_Send("ERROR: Cannot open /FW/app.sfb for writing.\r\n");
		FS_ResourceManager_ReleaseResource(FS_RESOURCE_FATFS);
		return;
	}

	write_total = 0;
	write_expected = size;
	cli_mode = CLI_MODE_BINARY_WRITE;
	FS_CLI_Send("READY\r\n");
}

/*---------------------------------------------------------------------------*/
/* Command dispatcher                                                        */
/*---------------------------------------------------------------------------*/

static void FS_CLI_Execute(const char *cmd)
{
	if (strcmp(cmd, "help") == 0)
	{
		FS_CLI_Send(
			"Available commands:\r\n"
			"  pair start             - Start BLE pairing mode\r\n"
			"  pair stop              - Stop BLE pairing mode\r\n"
			"  status                 - Show device mode and BLE state\r\n"
			"  track latest           - Show all files from latest track\r\n"
			"  flysight read          - Show flysight.txt\r\n"
			"  flysight write         - Write flysight.txt (end with Ctrl-D)\r\n"
			"  config read            - Show config.txt\r\n"
			"  config write           - Write config.txt (end with Ctrl-D)\r\n"
			"  file read <path>       - Read a text file (path: string, max 64 chars)\r\n"
			"  file write <path>      - Write a text file (path: string, max 64 chars, end with Ctrl-D)\r\n"
			"  temp list              - List contents of /temp folder\r\n"
			"  temp delete            - Delete all contents of /temp folder\r\n"
			"  fw upload <n>          - Upload firmware binary (n: file size in bytes, to /FW/app.sfb)\r\n"
			"  mode <state>           - Change device mode (state: sleep|active|start|config)\r\n"
			"  msc                    - Enable mass storage on next USB replug\r\n"
			"  version                - Show firmware version\r\n"
			"  help                   - Show this help\r\n"
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
	else if (strcmp(cmd, "track latest") == 0)
	{
		FS_CLI_CmdTrackLatest();
	}
	else if (strcmp(cmd, "flysight read") == 0)
	{
		FS_CLI_ReadFile("/flysight.txt");
	}
	else if (strcmp(cmd, "flysight write") == 0)
	{
		FS_CLI_WriteFile("/flysight.txt");
	}
	else if (strcmp(cmd, "config read") == 0)
	{
		FS_CLI_ReadFile("/config.txt");
	}
	else if (strcmp(cmd, "config write") == 0)
	{
		FS_CLI_WriteFile("/config.txt");
	}
	else if (strncmp(cmd, "file read ", 10) == 0)
	{
		FS_CLI_ReadFile(cmd + 10);
	}
	else if (strncmp(cmd, "file write ", 11) == 0)
	{
		FS_CLI_WriteFile(cmd + 11);
	}
	else if (strcmp(cmd, "temp list") == 0)
	{
		FS_CLI_CmdTempList();
	}
	else if (strcmp(cmd, "temp delete") == 0)
	{
		FS_CLI_CmdTempDelete();
	}
	else if (strcmp(cmd, "msc") == 0)
	{
		FS_CLI_CmdMSC();
	}
	else if (strncmp(cmd, "fw upload ", 10) == 0)
	{
		uint32_t size = strtoul(cmd + 10, NULL, 10);
		if (size > 0)
		{
			FS_CLI_CmdFwUpload(size);
		}
		else
		{
			FS_CLI_Send("Usage: fw upload <size_in_bytes>\r\n");
		}
	}
	else if (strncmp(cmd, "mode ", 5) == 0)
	{
		const char *target = cmd + 5;
		if (strcmp(target, "sleep") == 0)
			FS_Mode_ForceState(FS_MODE_STATE_SLEEP);
		else if (strcmp(target, "active") == 0)
			FS_Mode_ForceState(FS_MODE_STATE_ACTIVE);
		else if (strcmp(target, "start") == 0)
			FS_Mode_ForceState(FS_MODE_STATE_START);
		else if (strcmp(target, "config") == 0)
			FS_Mode_ForceState(FS_MODE_STATE_CONFIG);
		else
		{
			FS_CLI_Send("Usage: mode sleep|active|start|config\r\n");
			return;
		}
		snprintf(tx_buf, sizeof(tx_buf), "Mode changed to: %s\r\n", target);
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
