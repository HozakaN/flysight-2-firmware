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

#include "usbd_cdc_if.h"
#include "usbd_composite.h"
#include "cli.h"
#include "cli_transport.h"

/* Forward declarations of the CDC callback functions */
extern USBD_HandleTypeDef hUsbDeviceFS;

/* CDC Rx buffer */
static uint8_t UserRxBufferFS[CDC_DATA_FS_MAX_PACKET_SIZE];

/* CDC Tx buffer */
static uint8_t UserTxBufferFS[CDC_DATA_FS_MAX_PACKET_SIZE];

static int8_t CDC_Init_FS(void);
static int8_t CDC_DeInit_FS(void);
static int8_t CDC_Control_FS(uint8_t cmd, uint8_t *pbuf, uint16_t length);
static int8_t CDC_Receive_FS(uint8_t *Buf, uint32_t *Len);
static int8_t CDC_TransmitCplt_FS(uint8_t *Buf, uint32_t *Len, uint8_t epnum);

USBD_CDC_ItfTypeDef USBD_CDC_fops =
{
  CDC_Init_FS,
  CDC_DeInit_FS,
  CDC_Control_FS,
  CDC_Receive_FS,
  CDC_TransmitCplt_FS
};

static USBD_CDC_LineCodingTypeDef LineCoding =
{
  115200,  /* baud rate */
  0x00,    /* stop bits: 1 */
  0x00,    /* parity: none */
  0x08     /* data bits: 8 */
};

static int8_t CDC_Init_FS(void)
{
  /* Set classId for CDC operations */
  hUsbDeviceFS.classId = COMPOSITE_CDC_CLASS_ID;

  USBD_CDC_SetTxBuffer(&hUsbDeviceFS, UserTxBufferFS, 0);
  USBD_CDC_SetRxBuffer(&hUsbDeviceFS, UserRxBufferFS);
  return USBD_OK;
}

static int8_t CDC_DeInit_FS(void)
{
  return USBD_OK;
}

static int8_t CDC_Control_FS(uint8_t cmd, uint8_t *pbuf, uint16_t length)
{
  (void)length;

  switch (cmd)
  {
    case CDC_SEND_ENCAPSULATED_COMMAND:
      break;
    case CDC_GET_ENCAPSULATED_RESPONSE:
      break;
    case CDC_SET_COMM_FEATURE:
      break;
    case CDC_GET_COMM_FEATURE:
      break;
    case CDC_CLEAR_COMM_FEATURE:
      break;

    case CDC_SET_LINE_CODING:
      LineCoding.bitrate    = (uint32_t)(pbuf[0] | (pbuf[1] << 8) |
                                         (pbuf[2] << 16) | (pbuf[3] << 24));
      LineCoding.format     = pbuf[4];
      LineCoding.paritytype = pbuf[5];
      LineCoding.datatype   = pbuf[6];
      break;

    case CDC_GET_LINE_CODING:
      pbuf[0] = (uint8_t)(LineCoding.bitrate);
      pbuf[1] = (uint8_t)(LineCoding.bitrate >> 8);
      pbuf[2] = (uint8_t)(LineCoding.bitrate >> 16);
      pbuf[3] = (uint8_t)(LineCoding.bitrate >> 24);
      pbuf[4] = LineCoding.format;
      pbuf[5] = LineCoding.paritytype;
      pbuf[6] = LineCoding.datatype;
      break;

    case CDC_SET_CONTROL_LINE_STATE:
      break;
    case CDC_SEND_BREAK:
      break;

    default:
      break;
  }

  return USBD_OK;
}

static int8_t CDC_Receive_FS(uint8_t *Buf, uint32_t *Len)
{
  /* Forward received data to the CLI module */
  FS_CLI_SetActiveTransport(&cli_transport_cdc);
  FS_CLI_RxCallback(Buf, *Len);

  /* Re-arm the OUT endpoint for the next reception */
  hUsbDeviceFS.classId = COMPOSITE_CDC_CLASS_ID;
  USBD_CDC_SetRxBuffer(&hUsbDeviceFS, UserRxBufferFS);
  USBD_CDC_ReceivePacket(&hUsbDeviceFS);

  return USBD_OK;
}

static int8_t CDC_TransmitCplt_FS(uint8_t *Buf, uint32_t *Len, uint8_t epnum)
{
  (void)Buf;
  (void)Len;
  (void)epnum;
  return USBD_OK;
}

uint8_t CDC_Transmit_FS(uint8_t *Buf, uint16_t Len)
{
  USBD_CDC_HandleTypeDef *hcdc;
  uint8_t result;

  hUsbDeviceFS.classId = COMPOSITE_CDC_CLASS_ID;
  hcdc = (USBD_CDC_HandleTypeDef *)hUsbDeviceFS.pClassDataCmsit[hUsbDeviceFS.classId];

  if (hcdc == NULL)
  {
    return USBD_FAIL;
  }

  if (hcdc->TxState != 0U)
  {
    return USBD_BUSY;
  }

  USBD_CDC_SetTxBuffer(&hUsbDeviceFS, Buf, Len);
  result = USBD_CDC_TransmitPacket(&hUsbDeviceFS);

  return result;
}

uint8_t CDC_TxBusy_FS(void)
{
  USBD_CDC_HandleTypeDef *hcdc;

  hUsbDeviceFS.classId = COMPOSITE_CDC_CLASS_ID;
  hcdc = (USBD_CDC_HandleTypeDef *)hUsbDeviceFS.pClassDataCmsit[hUsbDeviceFS.classId];

  if (hcdc == NULL)
  {
    return 0U;
  }

  return (hcdc->TxState != 0U) ? 1U : 0U;
}

static uint8_t cdc_transport_transmit(const uint8_t *buf, uint16_t len)
{
  return CDC_Transmit_FS((uint8_t *)buf, len);
}

static uint8_t cdc_transport_tx_busy(void)
{
  return CDC_TxBusy_FS();
}

const FS_CLI_Transport_t cli_transport_cdc = {
  .transmit = cdc_transport_transmit,
  .tx_busy  = cdc_transport_tx_busy,
};
