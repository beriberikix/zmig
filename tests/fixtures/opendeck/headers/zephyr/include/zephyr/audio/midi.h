/*
 * Copyright (c) 2024 Titouan Christophe
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief Header file for MIDI 2.0 Universal MIDI Packet (UMP) definitions.
 * @ingroup midi_ump
 */

#ifndef ZEPHYR_INCLUDE_AUDIO_MIDI_H_
#define ZEPHYR_INCLUDE_AUDIO_MIDI_H_

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/**
 * @brief Universal MIDI Packet definitions
 * @defgroup midi_ump MIDI2 Universal MIDI Packet definitions
 * @ingroup audio_interface
 * @since 4.1
 * @version 0.1.0
 * @{
 */

/**
 * @defgroup ump112 Universal MIDI Packet (UMP) Format and MIDI 2.0 Protocol
 * @ingroup midi_ump
 * @{
 * @details Definitions based on the following document
 * <a href="https://midi.org/universal-midi-packet-ump-and-midi-2-0-protocol-specification">
 * Universal MIDI Packet (UMP) Format and MIDI 2.0 Protocol
 * With MIDI 1.0 Protocol in UMP Format - Document version 1.1.2
 * (MIDI Association Document: M2-104-UM)
 * </a>
 * @}
 */

/**
 * @brief      Universal MIDI Packet container
 */
struct midi_ump {
	uint32_t data[4]; /**< Raw content, in the CPU native endianness */
};

/**
 * @defgroup midi_ump_mt Message types
 * @ingroup midi_ump
 * @see ump112: 2.1.4 Message Type (MT) Allocation
 * @{
 */

/** Utility Messages */
#define UMP_MT_UTILITY             0x00
/** System Real Time and System Common Messages (except System Exclusive) */
#define UMP_MT_SYS_RT_COMMON       0x01
/** MIDI 1.0 Channel Voice Messages */
#define UMP_MT_MIDI1_CHANNEL_VOICE 0x02
/** 64 bits Data Messages (including System Exclusive) */
#define UMP_MT_DATA_64             0x03
/** MIDI 2.0 Channel Voice Messages */
#define UMP_MT_MIDI2_CHANNEL_VOICE 0x04
/** 128 bits Data Messages */
#define UMP_MT_DATA_128            0x05
/** Flex Data Messages */
#define UMP_MT_FLEX_DATA           0x0d
/**
 * UMP Stream Message
 * @see midi_ump_stream
 */
#define UMP_MT_UMP_STREAM          0x0f
/** @} */

/**
 * @brief      Message Type field of a Universal MIDI Packet
 * @param[in]  ump    Universal MIDI Packet
 * @see midi_ump_mt
 */
#define UMP_MT(ump) \
	((ump).data[0] >> 28)

/**
 * There are 16 UMP message types, each of which can be 1 to 4 uint32 long.
 * Hence this packed representation of 16x2b array as an uint32 lookup table
 */
#define UMP_NUM_WORDS_LOOKUP_TABLE \
	((0U << 0) | (0U << 2) | (0U << 4) | (1U << 6) | \
	(1U << 8) | (3U << 10) | (0U << 12) | (0U << 14) | \
	(1U << 16) | (1U << 18) | (1U << 20) | (2U << 22) | \
	(2U << 24) | (3U << 26) | (3U << 28) | (3U << 30))

/**
 * @brief      Size of a Universal MIDI Packet, in 32bit words
 * @param[in]  ump    Universal MIDI Packet
 * @see ump112: 2.1.4 Message Type (MT) Allocation
 */
#define UMP_NUM_WORDS(ump) \
	(1 + ((UMP_NUM_WORDS_LOOKUP_TABLE >> (2 * UMP_MT(ump))) & 3))

/**
 * @brief      MIDI group field of a Universal MIDI Packet
 * @param[in]  ump    Universal MIDI Packet
 */
