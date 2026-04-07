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

#include "cli_uart.h"
#include "cli.h"
#include "hw_if.h"
#include "main.h"

/* Single-byte RX buffer for interrupt-driven reception */
static uint8_t uart_rx_byte;

static void uart_rx_cb(void);

/*---------------------------------------------------------------------------*/
/* Transport callbacks                                                       */
/*---------------------------------------------------------------------------*/

static uint8_t uart_transmit(const uint8_t *buf, uint16_t len)
{
	hw_status_t st = HW_UART_Transmit(hw_lpuart1, (uint8_t *)buf, len, 1000);
	return (st == hw_uart_ok) ? 0 : 1;
}

static uint8_t uart_tx_busy(void)
{
	/* Blocking transmit: never busy after return */
	return 0;
}

const FS_CLI_Transport_t cli_transport_uart = {
	.transmit = uart_transmit,
	.tx_busy  = uart_tx_busy,
};

/*---------------------------------------------------------------------------*/
/* UART RX interrupt callback                                                */
/*---------------------------------------------------------------------------*/

static void uart_rx_cb(void)
{
	FS_CLI_SetActiveTransport(&cli_transport_uart);
	FS_CLI_RxCallback(&uart_rx_byte, 1);

	/* Re-arm for next byte */
	HW_UART_Receive_IT(hw_lpuart1, &uart_rx_byte, 1, uart_rx_cb);
}

/*---------------------------------------------------------------------------*/
/* Initialization                                                            */
/*---------------------------------------------------------------------------*/

void FS_CLI_UART_Init(void)
{
	static const char welcome[] = "\r\nFlySight CLI ready (UART). Type 'help' for commands.\r\n> ";

	/* Initialize LPUART1 peripheral (GPIO, DMA, NVIC via MSP callback) */
	MX_LPUART1_UART_Init();

	/* Send welcome message on UART */
	FS_CLI_SetActiveTransport(&cli_transport_uart);
	uart_transmit((const uint8_t *)welcome, sizeof(welcome) - 1);

	/* Start interrupt-driven single-byte reception */
	HW_UART_Receive_IT(hw_lpuart1, &uart_rx_byte, 1, uart_rx_cb);
}
