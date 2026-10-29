/** @file
 *  @brief DNS Service Discovery
 */

/*
 * Copyright (c) 2020 Friedt Professional Engineering Services, Inc
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef ZEPHYR_INCLUDE_NET_DNS_SD_H_
#define ZEPHYR_INCLUDE_NET_DNS_SD_H_

#include <stdint.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/sys/iterable_sections.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief DNS Service Discovery
 *
 * @details This API enables services to be advertised via DNS. To
 * advertise a service, system or application code should use
 * @ref DNS_SD_REGISTER_TCP_SERVICE or
 * @ref DNS_SD_REGISTER_UDP_SERVICE.
 *
 * @see @rfc{6763}
 *
 * @defgroup dns_sd DNS Service Discovery
 * @since 2.5
 * @version 0.8.0
 * @ingroup networking
 * @{
 */

/** @rfc{1034,section-3.1} */
#define DNS_SD_INSTANCE_MIN_SIZE 1
/** @rfc{1034,section-3.1}, @rfc{6763,section-7.2} */
#define DNS_SD_INSTANCE_MAX_SIZE 63
/** @rfc{6763,section-7.2} - inclusive of underscore */
#define DNS_SD_SERVICE_MIN_SIZE 2
/** @rfc{6763,section-7.2} - inclusive of underscore */
#define DNS_SD_SERVICE_MAX_SIZE 16
/** @rfc{6763,section-4.1.2} */
#define DNS_SD_SERVICE_PREFIX '_'
/** @rfc{6763,section-4.1.2} - either _tcp or _udp (case insensitive) */
#define DNS_SD_PROTO_SIZE 4
/** ICANN Rules for TLD naming */
#define DNS_SD_DOMAIN_MIN_SIZE 2
/** @rfc{1034,section-3.1}, @rfc{6763,section-7.2} */
#define DNS_SD_DOMAIN_MAX_SIZE 63

/**
 * Minimum number of segments in a fully-qualified name
 *
 * This represents FQN's of the form below
 * ```
 * <sn>._tcp.<domain>.
 * ```
 * Currently sub-types and service domains are unsupported and only the
 * "local" domain is supported. Specifically, that excludes the following:
 * ```
 * <sub>._sub.<sn>._tcp.<servicedomain>.<parentdomain>.
 * ```
 * @see @rfc{6763,section-7.2}
 */
#define DNS_SD_MIN_LABELS 3
/**
 * Maximum number of segments in a fully-qualified name
 *
 * This represents FQN's of the form below
 * ```
 * <instance>.<sn>._tcp.<domain>.
 * ```
 *
 * Currently sub-types and service domains are unsupported and only the
 * "local" domain is supported. Specifically, that excludes the following:
 * ```
 * <sub>._sub.<sn>._tcp.<servicedomain>.<parentdomain>.
 * ```
 * @see @rfc{6763,section-7.2}
 */
#define DNS_SD_MAX_LABELS 4

/**
 * @brief Register a service for DNS Service Discovery
 *
 * This macro should be used for advanced use cases. Two simple use cases are
 * when a custom @p _domain or a custom (non-standard) @p _proto is required.
 *
 * Another use case is when the port number is not preassigned. That could
 * be for a number of reasons, but the most common use case would be for
 * ephemeral port usage - i.e. when the service is bound using port number 0.
 * In that case, Zephyr (like other OS's) will simply choose an unused port.
 * When using ephemeral ports, it can be helpful to assign @p _port to the
 * @ref net_sockaddr_in.sin_port field of an IPv4 @ref net_sockaddr_in, or to the
 * @ref net_sockaddr_in6.sin6_port field of an IPv6 @ref net_sockaddr_in6.
 *
 * The service can be referenced using the @p _id variable.
 *
 * @param _id variable name for the DNS-SD service record
 * @param _instance name of the service instance such as "My HTTP Server"
 * @param _service name of the service, such as "_http"
 * @param _proto protocol used by the service - either "_tcp" or "_udp"
 * @param _domain the domain of the service, such as "local"
 * @param _text information for the DNS TXT record
 * @param _port a pointer to the port number that this service will use
 */
#define DNS_SD_REGISTER_SERVICE(_id, _instance, _service, _proto,	\
				_domain, _text, _port)			\
	static const STRUCT_SECTION_ITERABLE(dns_sd_rec, _id) = {	\
		.instance = _instance,					\
		.service = _service,					\
		.proto = _proto,					\
		.domain = _domain,					\
