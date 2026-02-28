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

#include "usbd_composite.h"
#include "usbd_msc.h"
#include "usbd_cdc.h"
#include "usbd_desc.h"

/* Forward declarations */
static uint8_t Composite_Init(USBD_HandleTypeDef *pdev, uint8_t cfgidx);
static uint8_t Composite_DeInit(USBD_HandleTypeDef *pdev, uint8_t cfgidx);
static uint8_t Composite_Setup(USBD_HandleTypeDef *pdev, USBD_SetupReqTypedef *req);
static uint8_t Composite_EP0_RxReady(USBD_HandleTypeDef *pdev);
static uint8_t Composite_DataIn(USBD_HandleTypeDef *pdev, uint8_t epnum);
static uint8_t Composite_DataOut(USBD_HandleTypeDef *pdev, uint8_t epnum);
static uint8_t *Composite_GetFSCfgDesc(uint16_t *length);
static uint8_t *Composite_GetHSCfgDesc(uint16_t *length);
static uint8_t *Composite_GetOtherSpeedCfgDesc(uint16_t *length);
static uint8_t *Composite_GetDeviceQualifierDesc(uint16_t *length);

USBD_ClassTypeDef USBD_Composite =
{
  Composite_Init,
  Composite_DeInit,
  Composite_Setup,
  NULL,                     /* EP0_TxSent */
  Composite_EP0_RxReady,
  Composite_DataIn,
  Composite_DataOut,
  NULL,                     /* SOF */
  NULL,                     /* IsoINIncomplete */
  NULL,                     /* IsoOUTIncomplete */
  Composite_GetHSCfgDesc,
  Composite_GetFSCfgDesc,
  Composite_GetOtherSpeedCfgDesc,
  Composite_GetDeviceQualifierDesc,
};

/*
 * Composite Configuration Descriptor:
 *   Config (9) + IAD (8) + CDC Comm IF (9) + CDC Func Descs (5+5+4+5=19)
 *   + CDC CMD EP (7) + CDC Data IF (9) + CDC Data EPs (7+7=14) + MSC IF (9) + MSC EPs (7+7=14)
 * Total = 9 + 8 + 9 + 19 + 7 + 9 + 14 + 9 + 14 = 98
 *
 * Note: MSC is placed FIRST in interface numbering (interface 0) but CDC IAD
 * is described first in the descriptor to ensure Windows picks up the IAD properly.
 * Actually, let's put MSC first in the descriptor as interface 0 for compatibility.
 */

#define COMPOSITE_CONFIG_DESC_SIZ  98U

__ALIGN_BEGIN static uint8_t USBD_Composite_CfgDesc[COMPOSITE_CONFIG_DESC_SIZ] __ALIGN_END =
{
  /* Configuration Descriptor */
  0x09,                              /* bLength */
  USB_DESC_TYPE_CONFIGURATION,       /* bDescriptorType */
  LOBYTE(COMPOSITE_CONFIG_DESC_SIZ), /* wTotalLength */
  HIBYTE(COMPOSITE_CONFIG_DESC_SIZ),
  COMPOSITE_NUM_INTERFACES,          /* bNumInterfaces: 3 */
  0x01,                              /* bConfigurationValue */
  0x04,                              /* iConfiguration (string index) */
#if (USBD_SELF_POWERED == 1U)
  0xC0,                              /* bmAttributes: Self Powered */
#else
  0x80,                              /* bmAttributes: Bus Powered */
#endif
  USBD_MAX_POWER,                    /* MaxPower (mA) */

  /*---------------------------------------------------------------------------*/
  /* MSC Interface Descriptor (Interface 0) */
  /*---------------------------------------------------------------------------*/
  0x09,                              /* bLength */
  USB_DESC_TYPE_INTERFACE,           /* bDescriptorType */
  COMPOSITE_MSC_INTERFACE,           /* bInterfaceNumber: 0 */
  0x00,                              /* bAlternateSetting */
  0x02,                              /* bNumEndpoints: 2 */
  0x08,                              /* bInterfaceClass: MSC */
  0x06,                              /* bInterfaceSubClass: SCSI transparent */
  0x50,                              /* bInterfaceProtocol: BOT */
  0x05,                              /* iInterface (string index) */

  /* MSC Endpoint IN */
  0x07,                              /* bLength */
  USB_DESC_TYPE_ENDPOINT,            /* bDescriptorType */
  COMPOSITE_MSC_EPIN_ADDR,           /* bEndpointAddress: 0x81 */
  0x02,                              /* bmAttributes: Bulk */
  LOBYTE(MSC_MAX_FS_PACKET),         /* wMaxPacketSize: 64 */
  HIBYTE(MSC_MAX_FS_PACKET),
  0x00,                              /* bInterval */

  /* MSC Endpoint OUT */
  0x07,                              /* bLength */
  USB_DESC_TYPE_ENDPOINT,            /* bDescriptorType */
  COMPOSITE_MSC_EPOUT_ADDR,          /* bEndpointAddress: 0x01 */
  0x02,                              /* bmAttributes: Bulk */
  LOBYTE(MSC_MAX_FS_PACKET),         /* wMaxPacketSize: 64 */
  HIBYTE(MSC_MAX_FS_PACKET),
  0x00,                              /* bInterval */

  /*---------------------------------------------------------------------------*/
  /* IAD for CDC (groups interfaces 1 and 2) */
  /*---------------------------------------------------------------------------*/
  0x08,                              /* bLength: IAD Descriptor size */
  USB_DESC_TYPE_IAD,                 /* bDescriptorType: IAD (0x0B) */
  COMPOSITE_CDC_CMD_INTERFACE,       /* bFirstInterface: 1 */
  0x02,                              /* bInterfaceCount: 2 */
  0x02,                              /* bFunctionClass: CDC */
  0x02,                              /* bFunctionSubClass: ACM */
  0x01,                              /* bFunctionProtocol: AT Commands */
  0x00,                              /* iFunction */

  /*---------------------------------------------------------------------------*/
  /* CDC Communication Interface (Interface 1) */
  /*---------------------------------------------------------------------------*/
  0x09,                              /* bLength */
  USB_DESC_TYPE_INTERFACE,           /* bDescriptorType */
  COMPOSITE_CDC_CMD_INTERFACE,       /* bInterfaceNumber: 1 */
  0x00,                              /* bAlternateSetting */
  0x01,                              /* bNumEndpoints: 1 (notification) */
  0x02,                              /* bInterfaceClass: CDC */
  0x02,                              /* bInterfaceSubClass: ACM */
  0x01,                              /* bInterfaceProtocol: AT Commands */
  0x00,                              /* iInterface */

  /* CDC Header Functional Descriptor */
  0x05,                              /* bLength */
  0x24,                              /* bDescriptorType: CS_INTERFACE */
  0x00,                              /* bDescriptorSubtype: Header */
  0x10, 0x01,                        /* bcdCDC: 1.10 */

  /* CDC Call Management Functional Descriptor */
  0x05,                              /* bLength */
  0x24,                              /* bDescriptorType: CS_INTERFACE */
  0x01,                              /* bDescriptorSubtype: Call Management */
  0x00,                              /* bmCapabilities: D0+D1 */
  COMPOSITE_CDC_DATA_INTERFACE,      /* bDataInterface: 2 */

  /* CDC ACM Functional Descriptor */
  0x04,                              /* bLength */
  0x24,                              /* bDescriptorType: CS_INTERFACE */
  0x02,                              /* bDescriptorSubtype: ACM */
  0x02,                              /* bmCapabilities: line coding + serial state */

  /* CDC Union Functional Descriptor */
  0x05,                              /* bLength */
  0x24,                              /* bDescriptorType: CS_INTERFACE */
  0x06,                              /* bDescriptorSubtype: Union */
  COMPOSITE_CDC_CMD_INTERFACE,       /* bMasterInterface: 1 */
  COMPOSITE_CDC_DATA_INTERFACE,      /* bSlaveInterface0: 2 */

  /* CDC Notification Endpoint (Interrupt IN) */
  0x07,                              /* bLength */
  USB_DESC_TYPE_ENDPOINT,            /* bDescriptorType */
  COMPOSITE_CDC_CMD_EP,              /* bEndpointAddress: 0x83 */
  0x03,                              /* bmAttributes: Interrupt */
  LOBYTE(CDC_CMD_PACKET_SIZE),       /* wMaxPacketSize: 8 */
  HIBYTE(CDC_CMD_PACKET_SIZE),
  CDC_FS_BINTERVAL,                  /* bInterval */

  /*---------------------------------------------------------------------------*/
  /* CDC Data Interface (Interface 2) */
  /*---------------------------------------------------------------------------*/
  0x09,                              /* bLength */
  USB_DESC_TYPE_INTERFACE,           /* bDescriptorType */
  COMPOSITE_CDC_DATA_INTERFACE,      /* bInterfaceNumber: 2 */
  0x00,                              /* bAlternateSetting */
  0x02,                              /* bNumEndpoints: 2 */
  0x0A,                              /* bInterfaceClass: CDC Data */
  0x00,                              /* bInterfaceSubClass */
  0x00,                              /* bInterfaceProtocol */
  0x00,                              /* iInterface */

  /* CDC Data Endpoint IN */
  0x07,                              /* bLength */
  USB_DESC_TYPE_ENDPOINT,            /* bDescriptorType */
  COMPOSITE_CDC_IN_EP,               /* bEndpointAddress: 0x82 */
  0x02,                              /* bmAttributes: Bulk */
  LOBYTE(CDC_DATA_FS_MAX_PACKET_SIZE), /* wMaxPacketSize: 64 */
  HIBYTE(CDC_DATA_FS_MAX_PACKET_SIZE),
  0x00,                              /* bInterval */

  /* CDC Data Endpoint OUT */
  0x07,                              /* bLength */
  USB_DESC_TYPE_ENDPOINT,            /* bDescriptorType */
  COMPOSITE_CDC_OUT_EP,              /* bEndpointAddress: 0x02 */
  0x02,                              /* bmAttributes: Bulk */
  LOBYTE(CDC_DATA_FS_MAX_PACKET_SIZE), /* wMaxPacketSize: 64 */
  HIBYTE(CDC_DATA_FS_MAX_PACKET_SIZE),
  0x00,                              /* bInterval */
};

/* Device Qualifier descriptor for FS device */
__ALIGN_BEGIN static uint8_t USBD_Composite_DeviceQualifierDesc[USB_LEN_DEV_QUALIFIER_DESC] __ALIGN_END =
{
  USB_LEN_DEV_QUALIFIER_DESC,
  USB_DESC_TYPE_DEVICE_QUALIFIER,
  0x00, 0x02,
  0xEF,                              /* bDeviceClass: Miscellaneous */
  0x02,                              /* bDeviceSubClass: Common Class */
  0x01,                              /* bDeviceProtocol: IAD */
  USB_MAX_EP0_SIZE,
  0x01,
  0x00,
};

/*---------------------------------------------------------------------------*/
/* Composite class callbacks                                                 */
/*---------------------------------------------------------------------------*/

static uint8_t Composite_Init(USBD_HandleTypeDef *pdev, uint8_t cfgidx)
{
  /* Initialize MSC */
  pdev->classId = COMPOSITE_MSC_CLASS_ID;
  USBD_MSC_Init(pdev, cfgidx);

  /* Initialize CDC */
  pdev->classId = COMPOSITE_CDC_CLASS_ID;
  USBD_CDC.Init(pdev, cfgidx);

  return (uint8_t)USBD_OK;
}

static uint8_t Composite_DeInit(USBD_HandleTypeDef *pdev, uint8_t cfgidx)
{
  /* DeInit MSC */
  pdev->classId = COMPOSITE_MSC_CLASS_ID;
  USBD_MSC_DeInit(pdev, cfgidx);

  /* DeInit CDC */
  pdev->classId = COMPOSITE_CDC_CLASS_ID;
  USBD_CDC.DeInit(pdev, cfgidx);

  return (uint8_t)USBD_OK;
}

static uint8_t Composite_Setup(USBD_HandleTypeDef *pdev, USBD_SetupReqTypedef *req)
{
  uint8_t recipient = req->bmRequest & USB_REQ_RECIPIENT_MASK;

  if (recipient == USB_REQ_RECIPIENT_INTERFACE)
  {
    uint16_t iface = req->wIndex & 0xFFU;

    if (iface == COMPOSITE_MSC_INTERFACE)
    {
      pdev->classId = COMPOSITE_MSC_CLASS_ID;
      return USBD_MSC_Setup(pdev, req);
    }
    else if (iface == COMPOSITE_CDC_CMD_INTERFACE ||
             iface == COMPOSITE_CDC_DATA_INTERFACE)
    {
      pdev->classId = COMPOSITE_CDC_CLASS_ID;
      return USBD_CDC.Setup(pdev, req);
    }
  }
  else if (recipient == USB_REQ_RECIPIENT_ENDPOINT)
  {
    uint8_t ep = req->wIndex & 0xFFU;

    if (ep == COMPOSITE_MSC_EPIN_ADDR || ep == COMPOSITE_MSC_EPOUT_ADDR)
    {
      pdev->classId = COMPOSITE_MSC_CLASS_ID;
      return USBD_MSC_Setup(pdev, req);
    }
    else if (ep == COMPOSITE_CDC_IN_EP || ep == COMPOSITE_CDC_OUT_EP ||
             ep == COMPOSITE_CDC_CMD_EP)
    {
      pdev->classId = COMPOSITE_CDC_CLASS_ID;
      return USBD_CDC.Setup(pdev, req);
    }
  }

  return (uint8_t)USBD_OK;
}

static uint8_t Composite_EP0_RxReady(USBD_HandleTypeDef *pdev)
{
  /* CDC needs EP0 RxReady for SET_LINE_CODING etc. */
  pdev->classId = COMPOSITE_CDC_CLASS_ID;
  if (USBD_CDC.EP0_RxReady != NULL)
  {
    return USBD_CDC.EP0_RxReady(pdev);
  }
  return (uint8_t)USBD_OK;
}

static uint8_t Composite_DataIn(USBD_HandleTypeDef *pdev, uint8_t epnum)
{
  if (epnum == (COMPOSITE_MSC_EPIN_ADDR & 0x7FU))
  {
    pdev->classId = COMPOSITE_MSC_CLASS_ID;
    return USBD_MSC_DataIn(pdev, epnum);
  }
  else if (epnum == (COMPOSITE_CDC_IN_EP & 0x7FU) ||
           epnum == (COMPOSITE_CDC_CMD_EP & 0x7FU))
  {
    pdev->classId = COMPOSITE_CDC_CLASS_ID;
    return USBD_CDC.DataIn(pdev, epnum);
  }
  return (uint8_t)USBD_OK;
}

static uint8_t Composite_DataOut(USBD_HandleTypeDef *pdev, uint8_t epnum)
{
  if (epnum == (COMPOSITE_MSC_EPOUT_ADDR & 0x7FU))
  {
    pdev->classId = COMPOSITE_MSC_CLASS_ID;
    return USBD_MSC_DataOut(pdev, epnum);
  }
  else if (epnum == (COMPOSITE_CDC_OUT_EP & 0x7FU))
  {
    pdev->classId = COMPOSITE_CDC_CLASS_ID;
    return USBD_CDC.DataOut(pdev, epnum);
  }
  return (uint8_t)USBD_OK;
}

static uint8_t *Composite_GetFSCfgDesc(uint16_t *length)
{
  *length = (uint16_t)sizeof(USBD_Composite_CfgDesc);
  return USBD_Composite_CfgDesc;
}

static uint8_t *Composite_GetHSCfgDesc(uint16_t *length)
{
  *length = (uint16_t)sizeof(USBD_Composite_CfgDesc);
  return USBD_Composite_CfgDesc;
}

static uint8_t *Composite_GetOtherSpeedCfgDesc(uint16_t *length)
{
  *length = (uint16_t)sizeof(USBD_Composite_CfgDesc);
  return USBD_Composite_CfgDesc;
}

static uint8_t *Composite_GetDeviceQualifierDesc(uint16_t *length)
{
  *length = (uint16_t)sizeof(USBD_Composite_DeviceQualifierDesc);
  return USBD_Composite_DeviceQualifierDesc;
}
