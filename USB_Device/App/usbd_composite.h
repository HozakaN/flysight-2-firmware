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

#ifndef USBD_COMPOSITE_H_
#define USBD_COMPOSITE_H_

#ifdef __cplusplus
extern "C" {
#endif

#include "usbd_def.h"

/* MSC endpoints (keep existing) */
#define COMPOSITE_MSC_EPIN_ADDR    0x81U
#define COMPOSITE_MSC_EPOUT_ADDR   0x01U

/* CDC endpoints (new) */
#define COMPOSITE_CDC_IN_EP        0x82U
#define COMPOSITE_CDC_OUT_EP       0x02U
#define COMPOSITE_CDC_CMD_EP       0x83U

/* Interface numbers (MSC-only mode) */
#define MSC_ONLY_INTERFACE            0
#define MSC_ONLY_NUM_INTERFACES       1

/* Interface numbers (CDC-only mode) */
#define CDC_ONLY_CMD_INTERFACE        0
#define CDC_ONLY_DATA_INTERFACE       1
#define CDC_ONLY_NUM_INTERFACES       2

/* Class IDs for pClassDataCmsit / pUserData indexing */
#define COMPOSITE_MSC_CLASS_ID  0
#define COMPOSITE_CDC_CLASS_ID  1

extern USBD_ClassTypeDef USBD_Composite;
extern USBD_DescriptorsTypeDef Composite_Desc;

void USBD_Composite_SetMSCEnabled(uint8_t enabled);
uint8_t USBD_Composite_IsMSCEnabled(void);

#ifdef __cplusplus
}
#endif

#endif /* USBD_COMPOSITE_H_ */
