/*
 * SPDX-License-Identifier: Apache-2.0
 * Copyright (c) 2020 Nordic Semiconductor
 * Copyright (c) 2020, Linaro Ltd.
 * Copyright (c) 2025 The Zephyr Project Contributors
 *
 * Not a generated file. Feel free to modify.
 */

/**
 * @file
 * @brief Devicetree main header
 *
 * API for accessing the current application's devicetree macros.
 */

#ifndef ZEPHYR_INCLUDE_DEVICETREE_H_
#define ZEPHYR_INCLUDE_DEVICETREE_H_

#include <zephyr/devicetree_generated.h>
#include <zephyr/irq_multilevel.h>

#if !defined(_LINKER) && !defined(_ASMLANGUAGE)
#include <stdint.h>
#endif

#include <zephyr/sys/util.h>

/**
 * @brief devicetree.h API
 * @defgroup devicetree Devicetree
 * @since 2.2
 * @version 1.2.0
 * @{
 * @}
 */

/*
 * Property suffixes
 * -----------------
 *
 * These are the optional parts that come after the _P_<property>
 * part in DT_N_<path-id>_P_<property-id> macros, or the "prop-suf"
 * nonterminal in the DT guide's macros.bnf file.
 *
 * Before adding new ones, check this list to avoid conflicts. If any
 * are missing from this list, please add them. It should be complete.
 *
 * _ENUM_IDX: property's value as an index into bindings enum
 * _ENUM_VAL_<val>_EXISTS property's value as a token exists
 * _EXISTS: property is defined
 * _FOREACH_PROP_ELEM: helper for "iterating" over values in the property
 * _FOREACH_PROP_ELEM_VARGS: foreach functions with variable number of arguments
 * _IDX_<i>: logical index into property
 * _IDX_<i>_EXISTS: logical index into property is defined
 * _IDX_<i>_PH: phandle array's phandle by index (or phandle, phandles)
 * _IDX_<i>_STRING_TOKEN: string array element value as a token
 * _IDX_<i>_STRING_UPPER_TOKEN: string array element value as a uppercased token
 * _IDX <i>_STRING_UNQUOTED: string array element value as a sequence of tokens, with no quotes
 * _IDX_<i>_VAL_<val>: phandle array's specifier value by index
 * _IDX_<i>_VAL_<val>_EXISTS: cell value exists, by index
 * _LEN: property logical length
 * _NAME_<name>_PH: phandle array's phandle by name
 * _NAME_<name>_VAL_<val>: phandle array's property specifier by name
 * _NAME_<name>_VAL_<val>_EXISTS: cell value exists, by name
 * _STRING_TOKEN: string property's value as a token
 * _STRING_UPPER_TOKEN: like _STRING_TOKEN, but uppercased
 * _STRING_UNQUOTED: string property's value as a sequence of tokens, with no quotes
 */

/**
 * @defgroup devicetree-generic-id Node identifiers and helpers
 * @ingroup devicetree
 * @{
 */

/**
 * @brief Name for an invalid node identifier
 *
 * This supports cases where factored macros can be invoked from paths where
 * devicetree data may or may not be available. It is a preprocessor identifier
 * that does not match any valid devicetree node identifier.
 */
#define DT_INVALID_NODE _

/**
 * @brief Node identifier for the root node in the devicetree
 */
#define DT_ROOT DT_N

/**
 * @brief Get a node identifier for a devicetree path
 *
 * @note This macro returns a node identifier from path components. To get
 *       a path string from a node identifier, use DT_NODE_PATH() instead.
 *
 * The arguments to this macro are the names of non-root nodes in the
 * tree required to reach the desired node, starting from the root.
 * Non-alphanumeric characters in each name must be converted to
 * underscores to form valid C tokens, and letters must be lowercased.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             soc {
 *                     serial1: serial@40001000 {
 *                             status = "okay";
 *                             current-speed = <115200>;
 *                             ...
 *                     };
 *             };
 *     };
 * @endcode
 *
 * You can use `DT_PATH(soc, serial_40001000)` to get a node identifier
 * for the `serial@40001000` node. Node labels like `serial1` cannot be
 * used as DT_PATH() arguments; use DT_NODELABEL() for those instead.
 *
 * Example usage with DT_PROP() to get the `current-speed` property:
 *
 * @code{.c}
 *     DT_PROP(DT_PATH(soc, serial_40001000), current_speed) // 115200
 * @endcode
 *
 * (The `current-speed` property is also in `lowercase-and-underscores`
 * form when used with this API.)
 *
 * When determining arguments to DT_PATH():
 *
 * - the first argument corresponds to a child node of the root (`soc` above)
 * - a second argument corresponds to a child of the first argument
 *   (`serial_40001000` above, from the node name `serial@40001000`
 *   after lowercasing and changing `@` to `_`)
 * - and so on for deeper nodes in the desired node's path
 *
 * @param ... lowercase-and-underscores node names along the node's path,
 *            with each name given as a separate argument
 * @return node identifier for the node with that path
 */
#define DT_PATH(...) DT_PATH_INTERNAL(__VA_ARGS__)

/**
 * @brief Get a node identifier for a node label
 *
 * Convert non-alphanumeric characters in the node label to
 * underscores to form valid C tokens, and lowercase all letters. Note
 * that node labels are not the same thing as label properties.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     serial1: serial@40001000 {
 *             label = "UART_0";
 *             status = "okay";
 *             current-speed = <115200>;
 *             ...
 *     };
 * @endcode
 *
 * The only node label in this example is `serial1`.
 *
 * The string `UART_0` is *not* a node label; it's the value of a
 * property named label.
 *
 * You can use `DT_NODELABEL(serial1)` to get a node identifier for the
 * `serial@40001000` node. Example usage with DT_PROP() to get the
 * current-speed property:
 *
 * @code{.c}
 *     DT_PROP(DT_NODELABEL(serial1), current_speed) // 115200
 * @endcode
 *
 * Another example devicetree fragment:
 *
 * @code{.dts}
 *     cpu@0 {
 *            L2_0: l2-cache {
 *                    cache-level = <2>;
 *                    ...
 *            };
 *     };
 * @endcode
 *
 * Example usage to get the cache-level property:
 *
 * @code{.c}
 *     DT_PROP(DT_NODELABEL(l2_0), cache_level) // 2
 * @endcode
 *
 * Notice how `L2_0` in the devicetree is lowercased to `l2_0` in the
 * DT_NODELABEL() argument.
 *
 * @param label lowercase-and-underscores node label name
 * @return node identifier for the node with that label
 */
#define DT_NODELABEL(label) DT_CAT(DT_N_NODELABEL_, label)

/**
 * @brief Get the C symbolic name of a node identifier by index
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     n1: node-1 {
 *             foo = <&n2 &n3>;
 *     };
 *
 *     n2: n2_1: node-2 { ... };
 *     n3: &n2:  node-3 { ... };
 * @endcode
 *
 * Above, `foo` has type phandles and has two elements:
 *
 * - index 0 has phandle `&n2`, which is `node-2`'s phandle
 * - index 1 has phandle `&n3`, which is `node-3`'s phandle
 *
 * Example usage:
 *
 * @code{.c}
 *     #define N1 DT_NODELABEL(n1)
 *
 *     DT_NODELABEL_C_TOKEN_BY_IDX(DT_PHANDLE_BY_IDX(N1, foo, 0), 0) // n2
 *     DT_NODELABEL_C_TOKEN_BY_IDX(DT_PHANDLE_BY_IDX(N1, foo, 0), 1) // n2_1
 *     DT_NODELABEL_C_TOKEN_BY_IDX(DT_PHANDLE_BY_IDX(N1, foo, 1), 0) // n2
 *     DT_NODELABEL_C_TOKEN_BY_IDX(DT_PHANDLE_BY_IDX(N1, foo, 1), 1) // n2_1
 *     DT_NODELABEL_C_TOKEN_BY_IDX(DT_PHANDLE_BY_IDX(N1, foo, 1), 2) // n3
 * @endcode
 *
 * @param node_id node identifier
 * @param idx index into @p node_id
 * @return C symbolic name of the node for the given index
 * @note The @p idx retrieves node labels in left-to-right order, as shown
 *       in the example.
 */
#define DT_NODELABEL_C_TOKEN_BY_IDX(node_id, idx) \
	DT_CAT5(DT_N_NODELABEL_, node_id, _IDX_, idx, _C_TOKEN)

/**
 * @brief Get the C symbolic name for a node identifier
 *
 * This is equivalent to DT_NODELABEL_C_TOKEN_BY_IDX(node_id, 0).
 *
 * @param node_id node identifier
 * @return C symbolic name of the node
 */
#define DT_NODELABEL_C_TOKEN(node_id) \
	DT_NODELABEL_C_TOKEN_BY_IDX(node_id, 0)

/**
 * @brief Get a node identifier from /aliases
 *
 * This macro's argument is a property of the `/aliases` node. It
 * returns a node identifier for the node which is aliased. Convert
 * non-alphanumeric characters in the alias property to underscores to
 * form valid C tokens, and lowercase all letters.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             aliases {
 *                     my-serial = &serial1;
 *             };
 *
 *             soc {
 *                     serial1: serial@40001000 {
 *                             status = "okay";
 *                             current-speed = <115200>;
 *                             ...
 *                     };
 *             };
 *     };
 * @endcode
 *
 * You can use DT_ALIAS(my_serial) to get a node identifier for the
 * `serial@40001000` node. Notice how `my-serial` in the devicetree
 * becomes `my_serial` in the DT_ALIAS() argument. Example usage with
 * DT_PROP() to get the current-speed property:
 *
 * @code{.c}
 *     DT_PROP(DT_ALIAS(my_serial), current_speed) // 115200
 * @endcode
 *
 * @param alias lowercase-and-underscores alias name.
 * @return node identifier for the node with that alias
 */
#define DT_ALIAS(alias) DT_CAT(DT_N_ALIAS_, alias)

/**
 * @brief Test if the devicetree has a given alias
 * @param alias_name lowercase-and-underscores devicetree alias name
 * @return 1 if the alias exists and refers to a node, 0 otherwise
 */
#define DT_HAS_ALIAS(alias_name) DT_NODE_EXISTS(DT_ALIAS(alias_name))

/**
 * @brief Get the hash associated with a DT node
 *
 * Get the hash for the specified node_id. The hash is calculated on the
 * full devicetree path of the node.
 * @param node_id node identifier
 * @return hash value as a preprocessor token
 */
#define DT_NODE_HASH(node_id) DT_CAT(node_id, _HASH)

/**
 * @brief Get a node identifier for an instance of a compatible
 *
 * All nodes with a particular compatible property value are assigned
 * instance numbers, which are zero-based indexes specific to that
 * compatible. You can get a node identifier for these nodes by
 * passing DT_INST() an instance number, @p inst, along with the
 * lowercase-and-underscores version of the compatible, @p compat.
 *
 * Instance numbers have the following properties:
 *
 * - for each compatible, instance numbers start at 0 and are contiguous
 * - exactly one instance number is assigned for each node with a compatible,
 *   **including disabled nodes**
 * - enabled nodes (status property is `okay` or missing) are assigned the
 *   instance numbers starting from 0, and disabled nodes have instance
 *   numbers which are greater than those of any enabled node
 *
 * No other guarantees are made. In particular:
 *
 * - instance numbers **in no way reflect** any numbering scheme that
 *   might exist in SoC documentation, node labels or unit addresses,
 *   or properties of the /aliases node (use DT_NODELABEL() or DT_ALIAS()
 *   for those)
 * - there **is no general guarantee** that the same node will have
 *   the same instance number between builds, even if you are building
 *   the same application again in the same build directory
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     serial1: serial@40001000 {
 *             compatible = "vnd,soc-serial";
 *             status = "disabled";
 *             current-speed = <9600>;
 *             ...
 *     };
 *
 *     serial2: serial@40002000 {
 *             compatible = "vnd,soc-serial";
 *             status = "okay";
 *             current-speed = <57600>;
 *             ...
 *     };
 *
 *     serial3: serial@40003000 {
 *             compatible = "vnd,soc-serial";
 *             current-speed = <115200>;
 *             ...
 *     };
 * @endcode
 *
 * Assuming no other nodes in the devicetree have compatible
 * `"vnd,soc-serial"`, that compatible has nodes with instance numbers
 * 0, 1, and 2.
 *
 * The nodes `serial@40002000` and `serial@40003000` are both enabled, so
 * their instance numbers are 0 and 1, but no guarantees are made
 * regarding which node has which instance number.
 *
 * Since `serial@40001000` is the only disabled node, it has instance
 * number 2, since disabled nodes are assigned the largest instance
 * numbers. Therefore:
 *
 * @code{.c}
 *     // Could be 57600 or 115200. There is no way to be sure:
 *     // either serial@40002000 or serial@40003000 could
 *     // have instance number 0, so this could be the current-speed
 *     // property of either of those nodes.
 *     DT_PROP(DT_INST(0, vnd_soc_serial), current_speed)
 *
 *     // Could be 57600 or 115200, for the same reason.
 *     // If the above expression expands to 57600, then
 *     // this expands to 115200, and vice-versa.
 *     DT_PROP(DT_INST(1, vnd_soc_serial), current_speed)
 *
 *     // 9600, because there is only one disabled node, and
 *     // disabled nodes are "at the end" of the instance
 *     // number "list".
 *     DT_PROP(DT_INST(2, vnd_soc_serial), current_speed)
 * @endcode
 *
 * Notice how `"vnd,soc-serial"` in the devicetree becomes `vnd_soc_serial`
 * (without quotes) in the DT_INST() arguments. (As usual, `current-speed`
 * in the devicetree becomes `current_speed` as well.)
 *
 * Nodes whose `compatible` property has multiple values are assigned
 * independent instance numbers for each compatible.
 *
 * @param inst instance number for compatible @p compat
 * @param compat lowercase-and-underscores compatible, without quotes
 * @return node identifier for the node with that instance number and
 *         compatible
 */
#define DT_INST(inst, compat) UTIL_CAT(DT_N_INST, DT_DASH(inst, compat))

/**
 * @brief Get a node identifier for a parent node
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     parent: parent-node {
 *             child: child-node {
 *                     ...
 *             };
 *     };
 * @endcode
 *
 * The following are equivalent ways to get the same node identifier:
 *
 * @code{.c}
 *     DT_NODELABEL(parent)
 *     DT_PARENT(DT_NODELABEL(child))
 * @endcode
 *
 * @param node_id node identifier
 * @return a node identifier for the node's parent
 */
#define DT_PARENT(node_id) DT_CAT(node_id, _PARENT)

/**
 * @brief Get a node identifier for a grandparent node
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     gparent: grandparent-node {
 *             parent: parent-node {
 *                     child: child-node { ... }
 *             };
 *     };
 * @endcode
 *
 * The following are equivalent ways to get the same node identifier:
 *
 * @code{.c}
 *     DT_GPARENT(DT_NODELABEL(child))
 *     DT_PARENT(DT_PARENT(DT_NODELABEL(child))
 * @endcode
 *
 * @param node_id node identifier
 * @return a node identifier for the node's parent's parent
 */
#define DT_GPARENT(node_id) DT_PARENT(DT_PARENT(node_id))

/**
 * @brief Get a node identifier for a child node
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             soc-label: soc {
 *                     serial1: serial@40001000 {
 *                             status = "okay";
 *                             current-speed = <115200>;
 *                             ...
 *                     };
 *             };
 *     };
 * @endcode
 *
 * Example usage with DT_PROP() to get the status of the
 * `serial@40001000` node:
 *
 * @code{.c}
 *     #define SOC_NODE DT_NODELABEL(soc_label)
 *     DT_PROP(DT_CHILD(SOC_NODE, serial_40001000), status) // "okay"
 * @endcode
 *
 * Node labels like `serial1` cannot be used as the @p child argument
 * to this macro. Use DT_NODELABEL() for that instead.
 *
 * You can also use DT_FOREACH_CHILD() to iterate over node
 * identifiers for all of a node's children.
 *
 * @param node_id node identifier
 * @param child lowercase-and-underscores child node name
 * @return node identifier for the node with the name referred to by 'child'
 */
#define DT_CHILD(node_id, child) UTIL_CAT(node_id, DT_S_PREFIX(child))

/**
 * @brief Get a node identifier for a child node with a matching unit address
 *
 * @note Only works for children with unique integer unit addresses.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             soc-label: soc {
 *                     serial1: serial@40001000 {
 *                             status = "okay";
 *                             current-speed = <115200>;
 *                             ...
 *                     };
 *             };
 *     };
 * @endcode
 *
 * Example usage with DT_PROP() to get the status of the
 * `serial@40001000` node:
 *
 * @code{.c}
 *     #define SOC_NODE DT_NODELABEL(soc_label)
 *     DT_PROP(DT_CHILD_BY_UNIT_ADDR_INT(SOC_NODE, 1073745920), status) // "okay"
 * @endcode
 *
 * @param node_id node identifier
 * @param addr Integer unit address for the child node.
 *
 * @return node identifier for the child node with the specified unit address
 */
#define DT_CHILD_BY_UNIT_ADDR_INT(node_id, addr) \
	DT_CAT3(node_id, _CHILD_UNIT_ADDR_INT_, addr)

/**
 * @brief Get a node identifier for a status `okay` node with a compatible
 *
 * Use this if you want to get an arbitrary enabled node with a given
 * compatible, and you do not care which one you get. If any enabled
 * nodes with the given compatible exist, a node identifier for one
 * of them is returned. Otherwise, @ref DT_INVALID_NODE is returned.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *	node-a {
 *		compatible = "vnd,device";
 *		status = "okay";
 *	};
 *
 *	node-b {
 *		compatible = "vnd,device";
 *		status = "okay";
 *	};
 *
 *	node-c {
 *		compatible = "vnd,device";
 *		status = "disabled";
 *	};
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_COMPAT_GET_ANY_STATUS_OKAY(vnd_device)
 * @endcode
 *
 * This expands to a node identifier for either `node-a` or `node-b`.
 * It will not expand to a node identifier for `node-c`, because that
 * node does not have status `okay`.
 *
 * @param compat lowercase-and-underscores compatible, without quotes
 * @return node identifier for a node with that compatible, or
 *         @ref DT_INVALID_NODE
 */
#define DT_COMPAT_GET_ANY_STATUS_OKAY(compat)			\
	COND_CODE_1(DT_HAS_COMPAT_STATUS_OKAY(compat),	\
		    (DT_INST(0, compat)),		\
		    (DT_INVALID_NODE))

/**
 * @brief Get a devicetree node's full path as a string literal
 *
 * This returns the path to a node from a node identifier. To get a
 * node identifier from path components instead, use DT_PATH().
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             soc {
 *                     node: my-node@12345678 { ... };
 *             };
 *     };
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *    DT_NODE_PATH(DT_NODELABEL(node)) // "/soc/my-node@12345678"
 *    DT_NODE_PATH(DT_PATH(soc))       // "/soc"
 *    DT_NODE_PATH(DT_ROOT)            // "/"
 * @endcode
 *
 * @param node_id node identifier
 * @return the node's full path in the devicetree
 */
#define DT_NODE_PATH(node_id) DT_CAT(node_id, _PATH)

/**
 * @brief Get a devicetree node's name with unit-address as a string literal
 *
 * This returns the node name and unit-address from a node identifier.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             soc {
 *                     node: my-node@12345678 { ... };
 *             };
 *     };
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *    DT_NODE_FULL_NAME(DT_NODELABEL(node)) // "my-node@12345678"
 * @endcode
 *
 * @param node_id node identifier
 * @return the node's name with unit-address as a string in the devicetree
 */
#define DT_NODE_FULL_NAME(node_id) DT_CAT(node_id, _FULL_NAME)

/**
 * @brief Get the node's full name, including the unit-address, as an unquoted
 *        sequence of tokens
 *
 * This macro returns removed "the quotes" from the node's full name.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             soc {
 *                     node: my-node@12345678 { ... };
 *             };
 *     };
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *    DT_NODE_FULL_NAME_UNQUOTED(DT_NODELABEL(node)) // my-node@12345678
 * @endcode
 *
 * @param node_id node identifier
 * @return the node's full name with unit-address as a sequence of tokens,
 *         with no quotes
 */
#define DT_NODE_FULL_NAME_UNQUOTED(node_id) DT_CAT(node_id, _FULL_NAME_UNQUOTED)

/**
 * @brief Get the node's full name, including the unit-address, as a token.
 *
 * This macro returns removed "the quotes" from the node's full name and
 * converting any non-alphanumeric characters to underscores.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             soc {
 *                     node: my-node@12345678 { ... };
 *             };
 *     };
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *    DT_NODE_FULL_NAME_TOKEN(DT_NODELABEL(node)) // my_node_12345678
 * @endcode
 *
 * @param node_id node identifier
 * @return the node's full name with unit-address as a token, i.e. without any quotes
 *         and with special characters converted to underscores
 */
#define DT_NODE_FULL_NAME_TOKEN(node_id) DT_CAT(node_id, _FULL_NAME_TOKEN)

/**
 * @brief Like DT_NODE_FULL_NAME_TOKEN(), but uppercased.
 *
 * This macro returns removed "the quotes" from the node's full name,
 * converting any non-alphanumeric characters to underscores, and
 * capitalizing the result.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     / {
 *             soc {
 *                     node: my-node@12345678 { ... };
 *             };
 *     };
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *    DT_NODE_FULL_NAME_UPPER_TOKEN(DT_NODELABEL(node)) // MY_NODE_12345678
 * @endcode
 *
 * @param node_id node identifier
 * @return the node's full name with unit-address as an uppercased token,
 *         i.e. without any quotes and with special characters converted
 *         to underscores
 */
#define DT_NODE_FULL_NAME_UPPER_TOKEN(node_id) DT_CAT(node_id, _FULL_NAME_UPPER_TOKEN)

/**
 * @brief Get a devicetree node's index into its parent's list of children
 *
 * Indexes are zero-based.
 *
 * It is an error to use this macro with the root node.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     parent {
 *             c1: child-1 {};
 *             c2: child-2 {};
 *     };
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_NODE_CHILD_IDX(DT_NODELABEL(c1)) // 0
 *     DT_NODE_CHILD_IDX(DT_NODELABEL(c2)) // 1
 * @endcode
 *
 * @param node_id node identifier
 * @return the node's index in its parent node's list of children
 */
#define DT_NODE_CHILD_IDX(node_id) DT_CAT(node_id, _CHILD_IDX)

/**
 * @brief Get the number of child nodes of a given node
 *
 * @param node_id a node identifier
 * @return Number of child nodes
 */
#define DT_CHILD_NUM(node_id) DT_CAT(node_id, _CHILD_NUM)

/**
 * @brief Get the number of child nodes of a given node,
 *        whose status is okay
 *
 * @param node_id a node identifier
 * @return Number of child nodes which status are okay
 */
#define DT_CHILD_NUM_STATUS_OKAY(node_id) \
	DT_CAT(node_id, _CHILD_NUM_STATUS_OKAY)

/**
 * @brief Get the number of descendant nodes of a given node
 *        that are on the given bus,
 *        whose binding defines the given bus as their on-bus key
 *
 * @param node_id a node identifier
 * @param bus bus type string
 * @return Number of descendant nodes
 */
#define DT_DESCENDANT_NUM_ON_BUS(node_id, bus) \
	DT_CAT3(node_id, _DESCENDANT_NUM_ON_BUS_, bus)

/**
 * @brief Get the number of descendant nodes of a given node
 *        that are on the given bus,
 *        whose binding defines the given bus as their on-bus key,
 *        and whose status is okay
 *
 * @param node_id a node identifier
 * @param bus bus type string
 * @return Number of descendant nodes
 */
#define DT_DESCENDANT_NUM_ON_BUS_STATUS_OKAY(node_id, bus) \
	DT_CAT4(node_id, _DESCENDANT_NUM_ON_BUS_, bus, _STATUS_OKAY)

/**
 * @brief Do @p node_id1 and @p node_id2 refer to the same node?
 *
 * Both @p node_id1 and @p node_id2 must be node identifiers for nodes
 * that exist in the devicetree (if unsure, you can check with
 * DT_NODE_EXISTS()).
 *
 * The expansion evaluates to 0 or 1, but may not be a literal integer
 * 0 or 1.
 *
 * @internal
 * Implementation note: distinct nodes have distinct node identifiers.
 * See include/zephyr/devicetree/ordinals.h.
 * @endinternal
 *
 * @param node_id1 first node identifier
 * @param node_id2 second node identifier
 * @return an expression that evaluates to 1 if the node identifiers
 *         refer to the same node, and evaluates to 0 otherwise
 */
#define DT_SAME_NODE(node_id1, node_id2) \
	IS_EQ(DT_DEP_ORD(node_id1), DT_DEP_ORD(node_id2))

/**
 * @brief Get a devicetree node's node labels as an array of strings
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     foo: bar: node@deadbeef {};
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_NODELABEL_STRING_ARRAY(DT_NODELABEL(foo))
 * @endcode
 *
 * This expands to:
 *
 * @code{.c}
 *     { "foo", "bar", }
 * @endcode
 *
 * @param node_id node identifier
 * @return an array initializer for an array of the node's node labels as strings
 */
#define DT_NODELABEL_STRING_ARRAY(node_id) \
	{ DT_FOREACH_NODELABEL(node_id, DT_NODELABEL_STRING_ARRAY_ENTRY_INTERNAL) }

/**
 * @}
 */

/**
 * @defgroup devicetree-generic-prop Property accessors
 * @ingroup devicetree
 * @{
 */

/**
 * @brief Get a devicetree property value
 *
 * For properties whose bindings have the following types, this macro
 * expands to:
 *
 * - string: a string literal
 * - boolean: `0` if the property is false, or `1` if it is true
 * - int: the property's value as an integer literal
 * - array, uint8-array, string-array: an initializer expression in braces,
 *   whose elements are integer or string literals (like `{0, 1, 2}`,
 *   `{"hello", "world"}`, etc.)
 * - phandle: a node identifier for the node with that phandle
 *
 * A property's type is usually defined by its binding. In some
 * special cases, it has an assumed type defined by the devicetree
 * specification even when no binding is available: `compatible` has
 * type string-array, `status` has type string, and
 * `interrupt-controller` has type boolean.
 *
 * For other properties or properties with unknown type due to a
 * missing binding, behavior is undefined.
 *
 * For usage examples, see DT_PATH(), DT_ALIAS(), DT_NODELABEL(),
 * and DT_INST() above.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @return a representation of the property's value
 */
#define DT_PROP(node_id, prop) DT_CAT3(node_id, _P_, prop)

/**
 * @brief Get a property's logical length
 *
 * Here, "length" is a number of elements, which may differ from the
 * property's size in bytes.
 *
 * The return value depends on the property's type:
 *
 * - for types array, string-array, and uint8-array, this expands
 *   to the number of elements in the array
 * - for type phandles, this expands to the number of phandles
 * - for type phandle-array, this expands to the number of
 *   phandle and specifier blocks in the property
 * - for type phandle, this expands to 1 (so that a phandle
 *   can be treated as a degenerate case of phandles with length 1)
 * - for type string, this expands to 1 (so that a string can be
 *   treated as a degenerate case of string-array with length 1)
 *
 * These properties are handled as special cases:
 *
 * - reg property: use `DT_NUM_REGS(node_id)` instead
 * - interrupts property: use `DT_NUM_IRQS(node_id)` instead
 *
 * It is an error to use this macro with the `ranges`, `dma-ranges`, `reg`
 * or `interrupts` properties.
 *
 * For other properties, behavior is undefined.
 *
 * @param node_id node identifier
 * @param prop a lowercase-and-underscores property with a logical length
 * @return the property's length
 */
#define DT_PROP_LEN(node_id, prop) DT_CAT4(node_id, _P_, prop, _LEN)

/**
 * @brief Like DT_PROP_LEN(), but with a fallback to @p default_value
 *
 * If the property is defined (as determined by DT_NODE_HAS_PROP()),
 * this expands to DT_PROP_LEN(node_id, prop). The @p default_value
 * parameter is not expanded in this case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @param node_id node identifier
 * @param prop a lowercase-and-underscores property with a logical length
 * @param default_value a fallback value to expand to
 * @return the property's length or the given default value
 */
#define DT_PROP_LEN_OR(node_id, prop, default_value) \
	COND_CODE_1(DT_NODE_HAS_PROP(node_id, prop), \
		    (DT_PROP_LEN(node_id, prop)), (default_value))

/**
 * @brief Is index @p idx valid for an array type property?
 *
 * If this returns 1, then DT_PROP_BY_IDX(node_id, prop, idx) or
 * DT_PHA_BY_IDX(node_id, prop, idx, ...) are valid at index @p idx.
 * If it returns 0, it is an error to use those macros with that index.
 *
 * These properties are handled as special cases:
 *
 * - `reg` property: use DT_REG_HAS_IDX(node_id, idx) instead
 * - `interrupts` property: use DT_IRQ_HAS_IDX(node_id, idx) instead
 *
 * It is an error to use this macro with the `reg` or `interrupts` properties.
 *
 * @param node_id node identifier
 * @param prop a lowercase-and-underscores property with a logical length
 * @param idx index to check
 * @return An expression which evaluates to 1 if @p idx is a valid index
 *         into the given property, and 0 otherwise.
 */
#define DT_PROP_HAS_IDX(node_id, prop, idx) \
	IS_ENABLED(DT_CAT6(node_id, _P_, prop, _IDX_, idx, _EXISTS))

/**
 * @brief Is name @p name available in a `foo-names` property?
 *
 * This property is handled as special case:
 *
 * - `interrupts` property: use DT_IRQ_HAS_NAME(node_id, idx) instead
 *
 * It is an error to use this macro with the `interrupts` property.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *	nx: node-x {
 *		foos = <&bar xx yy>, <&baz xx zz>;
 *		foo-names = "event", "error";
 *		status = "okay";
 *	};
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_PROP_HAS_NAME(DT_NODELABEL(nx), foos, event)    // 1
 *     DT_PROP_HAS_NAME(DT_NODELABEL(nx), foos, failure)  // 0
 * @endcode
 *
 * @param node_id node identifier
 * @param prop a lowercase-and-underscores `prop-names` type property
 * @param name a lowercase-and-underscores name to check
 * @return An expression which evaluates to 1 if "name" is an available
 *         name into the given property, and 0 otherwise.
 */
#define DT_PROP_HAS_NAME(node_id, prop, name) \
	IS_ENABLED(DT_CAT6(node_id, _P_, prop, _NAME_, name, _EXISTS))

/**
 * @brief Get the value at index @p idx in an array type property
 *
 * It might help to read the argument order as being similar to
 * `node->property[index]`.
 *
 * The return value depends on the property's type:
 *
 * - for types array, string-array, uint8-array, and phandles,
 *   this expands to the idx-th array element as an
 *   integer, string literal, integer, and node identifier
 *   respectively
 *
 * - for type phandle, idx must be 0 and the expansion is a node
 *   identifier (this treats phandle like a phandles of length 1)
 *
 * - for type string, idx must be 0 and the expansion is the
 *   entire string (this treats string like string-array of length 1)
 *
 * These properties are handled as special cases:
 *
 * - `reg`: use DT_REG_ADDR_BY_IDX() or DT_REG_SIZE_BY_IDX() instead
 * - `interrupts`: use DT_IRQ_BY_IDX()
 * - `ranges`: use DT_NUM_RANGES()
 * - `dma-ranges`: it is an error to use this property with
 *   DT_PROP_BY_IDX()
 *
 * For properties of other types, behavior is undefined.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param idx the index to get
 * @return a representation of the idx-th element of the property
 */
#define DT_PROP_BY_IDX(node_id, prop, idx) \
	DT_CAT5(node_id, _P_, prop, _IDX_, idx)

/**
 * @brief Get the last element of an array type property
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 *
 * @return a representation of the last element of the property
 */
#define DT_PROP_LAST(node_id, prop) \
	DT_PROP_BY_IDX(node_id, prop, UTIL_DEC(DT_PROP_LEN(node_id, prop)))

/**
 * @brief Like DT_PROP(), but with a fallback to @p default_value
 *
 * If the value exists, this expands to DT_PROP(node_id, prop).
 * The @p default_value parameter is not expanded in this case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param default_value a fallback value to expand to
 * @return the property's value or @p default_value
 */
#define DT_PROP_OR(node_id, prop, default_value) \
	COND_CODE_1(DT_NODE_HAS_PROP(node_id, prop), \
		    (DT_PROP(node_id, prop)), (default_value))

/**
 * @brief Get a property array value's index into its enumeration values
 *
 * The return values start at zero.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     some_node: some-node {
 *         compat = "vend,enum-string-array";
 *         foos =
 *             <&phandle val1>,
 *             <&phandle val2>,
 *             <&phandle val3>;
 *         foo-names = "default", "option3", "option1";
 *     };
 * @endcode
 *
 * Example bindings fragment:
 *
 * @code{.yaml}
 * compatible: vend,enum-string-array
 * properties:
 *   foos:
 *     type: phandle-array
 *     description: |
 *       Explanation about what this phandle-array exactly is for.
 *
 *   foo-names:
 *     type: string-array
 *     description: |
 *       Some explanation about the available options
 *       default: explain default
 *       option1: explain option1
 *       option2: explain option2
 *       option3: explain option3
 *     enum:
 *       - default
 *       - option1
 *       - option2
 *       - option3
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_ENUM_IDX_BY_IDX(DT_NODELABEL(some_node), foo_names, 0) // 0
 *     DT_ENUM_IDX_BY_IDX(DT_NODELABEL(some_node), foo_names, 2) // 1
 * @endcode
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param idx the index to get
 * @return zero-based index of the property's value in its enum: list
 */
#define DT_ENUM_IDX_BY_IDX(node_id, prop, idx) \
	DT_CAT6(node_id, _P_, prop, _IDX_, idx, _ENUM_IDX)

/**
 * @brief Equivalent to @ref DT_ENUM_IDX_BY_IDX(node_id, prop, 0).
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @return zero-based index of the property's value in its enum: list
 */
#define DT_ENUM_IDX(node_id, prop) DT_ENUM_IDX_BY_IDX(node_id, prop, 0)

/**
 * @brief Like DT_ENUM_IDX_BY_IDX(), but with a fallback to a default enum index
 *
 * If the value exists, this expands to its zero based index value thanks to
 * DT_ENUM_IDX_BY_IDX(node_id, prop, idx).
 *
 * Otherwise, this expands to provided default index enum value.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param idx the index to get
 * @param default_idx_value a fallback index value to expand to
 * @return zero-based index of the property's value in its enum if present,
 *         default_idx_value otherwise
 */
#define DT_ENUM_IDX_BY_IDX_OR(node_id, prop, idx, default_idx_value) \
	COND_CODE_1(DT_PROP_HAS_IDX(node_id, prop, idx), \
		    (DT_ENUM_IDX_BY_IDX(node_id, prop, idx)), (default_idx_value))

/**
 * @brief Equivalent to DT_ENUM_IDX_BY_IDX_OR(node_id, prop, 0, default_idx_value).
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param default_idx_value a fallback index value to expand to
 * @return zero-based index of the property's value in its enum if present,
 *         default_idx_value otherwise
 */
#define DT_ENUM_IDX_OR(node_id, prop, default_idx_value) \
	DT_ENUM_IDX_BY_IDX_OR(node_id, prop, 0, default_idx_value)

/**
 * @brief Does a node enumeration property array have a given value?
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param idx the index to get
 * @param value lowercase-and-underscores enumeration value
 * @return 1 if the node property has the value @a value, 0 otherwise.
 */
#define DT_ENUM_HAS_VALUE_BY_IDX(node_id, prop, idx, value) \
	IS_ENABLED(DT_CAT8(node_id, _P_, prop, _IDX_, idx, _ENUM_VAL_, value, _EXISTS))

/**
 * @brief Does a node enumeration property have a given value?
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param value lowercase-and-underscores enumeration value
 * @return 1 if the node property has the value @a value, 0 otherwise.
 */
#define DT_ENUM_HAS_VALUE(node_id, prop, value) \
	IS_ENABLED(DT_CAT6(node_id, _P_, prop, _ENUM_VAL_, value, _EXISTS))

/**
 * @brief Get a string property's value as a token.
 *
 * This removes "the quotes" from a string property's value,
 * converting any non-alphanumeric characters to underscores. This can
 * be useful, for example, when programmatically using the value to
 * form a C variable or code.
 *
 * DT_STRING_TOKEN() can only be used for properties with string type.
 *
 * It is an error to use DT_STRING_TOKEN() in other circumstances.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     n1: node-1 {
 *             prop = "foo";
 *     };
 *     n2: node-2 {
 *             prop = "FOO";
 *     }
 *     n3: node-3 {
 *             prop = "123 foo";
 *     };
 * @endcode
 *
 * Example bindings fragment:
 *
 * @code{.yaml}
 *     properties:
 *       prop:
 *         type: string
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_STRING_TOKEN(DT_NODELABEL(n1), prop) // foo
 *     DT_STRING_TOKEN(DT_NODELABEL(n2), prop) // FOO
 *     DT_STRING_TOKEN(DT_NODELABEL(n3), prop) // 123_foo
 * @endcode
 *
 * Notice how:
 *
 * - Unlike C identifiers, the property values may begin with a
 *   number. It's the user's responsibility not to use such values as
 *   the name of a C identifier.
 *
 * - The uppercased `"FOO"` in the DTS remains `FOO` as a token. It is
 *   *not* converted to `foo`.
 *
 * - The whitespace in the DTS `"123 foo"` string is converted to
 *   `123_foo` as a token.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @return the value of @p prop as a token, i.e. without any quotes
 *         and with special characters converted to underscores
 */
#define DT_STRING_TOKEN(node_id, prop) \
	DT_CAT4(node_id, _P_, prop, _STRING_TOKEN)

/**
 * @brief Like DT_STRING_TOKEN(), but with a fallback to @p default_value
 *
 * If the value exists, this expands to DT_STRING_TOKEN(node_id, prop).
 * The @p default_value parameter is not expanded in this case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param default_value a fallback value to expand to
 * @return the property's value as a token, or @p default_value
 */
#define DT_STRING_TOKEN_OR(node_id, prop, default_value) \
	COND_CODE_1(DT_NODE_HAS_PROP(node_id, prop), \
		(DT_STRING_TOKEN(node_id, prop)), (default_value))

/**
 * @brief Like DT_STRING_TOKEN(), but uppercased.
 *
 * This removes "the quotes" from a string property's value,
 * converting any non-alphanumeric characters to underscores, and
 * capitalizing the result. This can be useful, for example, when
 * programmatically using the value to form a C variable or code.
 *
 * DT_STRING_UPPER_TOKEN() can only be used for properties with string type.
 *
 * It is an error to use DT_STRING_UPPER_TOKEN() in other circumstances.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     n1: node-1 {
 *             prop = "foo";
 *     };
 *     n2: node-2 {
 *             prop = "123 foo";
 *     };
 * @endcode
 *
 * Example bindings fragment:
 *
 * @code{.yaml}
 *     properties:
 *       prop:
 *         type: string
 *
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_STRING_UPPER_TOKEN(DT_NODELABEL(n1), prop) // FOO
 *     DT_STRING_UPPER_TOKEN(DT_NODELABEL(n2), prop) // 123_FOO
 * @endcode
 *
 * Notice how:
 *
 * - Unlike C identifiers, the property values may begin with a
 *   number. It's the user's responsibility not to use such values as
 *   the name of a C identifier.
 *
 * - The lowercased `"foo"` in the DTS becomes `FOO` as a token, i.e.
 *   it is uppercased.
 *
 * - The whitespace in the DTS `"123 foo"` string is converted to
 *   `123_FOO` as a token, i.e. it is uppercased and whitespace becomes
 *   an underscore.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @return the value of @p prop as an uppercased token, i.e. without
 *         any quotes and with special characters converted to underscores
 */
#define DT_STRING_UPPER_TOKEN(node_id, prop) \
	DT_CAT4(node_id, _P_, prop, _STRING_UPPER_TOKEN)

/**
 * @brief Like DT_STRING_UPPER_TOKEN(), but with a fallback to @p default_value
 *
 * If the value exists, this expands to DT_STRING_UPPER_TOKEN(node_id, prop).
 * The @p default_value parameter is not expanded in this case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param default_value a fallback value to expand to
 * @return the property's value as an uppercased token,
 *         or @p default_value
 */
#define DT_STRING_UPPER_TOKEN_OR(node_id, prop, default_value) \
	COND_CODE_1(DT_NODE_HAS_PROP(node_id, prop), \
		(DT_STRING_UPPER_TOKEN(node_id, prop)), (default_value))

/**
 * @brief Get a string property's value as an unquoted sequence of tokens
 *
 * This removes "the quotes" from string-valued properties.
 * That can be useful, for example,
 * when defining floating point values as a string in devicetree
 * that you would like to use to initialize a float or double variable in C.
 *
 * DT_STRING_UNQUOTED() can only be used for properties with string type.
 *
 * It is an error to use DT_STRING_UNQUOTED() in other circumstances.
 *
 * Example devicetree fragment:
 *
 *     n1: node-1 {
 *             prop = "12.7";
 *     };
 *     n2: node-2 {
 *             prop = "0.5";
 *     }
 *     n3: node-3 {
 *             prop = "A B C";
 *     };
 *
 * Example bindings fragment:
 *
 *     properties:
 *       prop:
 *         type: string
 *
 * Example usage:
 *
 *     DT_STRING_UNQUOTED(DT_NODELABEL(n1), prop) // 12.7
 *     DT_STRING_UNQUOTED(DT_NODELABEL(n2), prop) // 0.5
 *     DT_STRING_UNQUOTED(DT_NODELABEL(n3), prop) // A B C
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @return the property's value as a sequence of tokens, with no quotes
 */
#define DT_STRING_UNQUOTED(node_id, prop) \
	DT_CAT4(node_id, _P_, prop, _STRING_UNQUOTED)

/**
 * @brief Like DT_STRING_UNQUOTED(), but with a fallback to @p default_value
 *
 * If the value exists, this expands to DT_STRING_UNQUOTED(node_id, prop).
 * The @p default_value parameter is not expanded in this case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param default_value a fallback value to expand to
 * @return the property's value as a sequence of tokens, with no quotes,
 *         or @p default_value
 */
#define DT_STRING_UNQUOTED_OR(node_id, prop, default_value) \
	COND_CODE_1(DT_NODE_HAS_PROP(node_id, prop), \
		(DT_STRING_UNQUOTED(node_id, prop)), (default_value))

/**
 * @brief Get an element out of a string-array property as a token.
 *
 * This removes "the quotes" from an element in the array, and converts
 * non-alphanumeric characters to underscores. That can be useful, for example,
 * when programmatically using the value to form a C variable or code.
 *
 * DT_STRING_TOKEN_BY_IDX() can only be used for properties with
 * string-array type.
 *
 * It is an error to use DT_STRING_TOKEN_BY_IDX() in other circumstances.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     n1: node-1 {
 *             prop = "f1", "F2";
 *     };
 *     n2: node-2 {
 *             prop = "123 foo", "456 FOO";
 *     };
 * @endcode
 *
 * Example bindings fragment:
 *
 * @code{.yaml}
 *     properties:
 *       prop:
 *         type: string-array
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_STRING_TOKEN_BY_IDX(DT_NODELABEL(n1), prop, 0) // f1
 *     DT_STRING_TOKEN_BY_IDX(DT_NODELABEL(n1), prop, 1) // F2
 *     DT_STRING_TOKEN_BY_IDX(DT_NODELABEL(n2), prop, 0) // 123_foo
 *     DT_STRING_TOKEN_BY_IDX(DT_NODELABEL(n2), prop, 1) // 456_FOO
 * @endcode
 *
 * For more information, see @ref DT_STRING_TOKEN.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param idx the index to get
 * @return the element in @p prop at index @p idx as a token
 */
#define DT_STRING_TOKEN_BY_IDX(node_id, prop, idx) \
	DT_CAT6(node_id, _P_, prop, _IDX_, idx, _STRING_TOKEN)

/**
 * @brief Like DT_STRING_TOKEN_BY_IDX(), but with a fallback to @p default_value
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param idx the index to get
 * @param default_value a fallback value to expand to
 * @return the element in @p prop at index @p idx as a token, or @p default_value
 */
#define DT_STRING_TOKEN_BY_IDX_OR(node_id, prop, idx, default_value) \
	COND_CODE_1(DT_PROP_HAS_IDX(node_id, prop, idx), \
		    (DT_STRING_TOKEN_BY_IDX(node_id, prop, idx)), (default_value))

/**
 * @brief Like DT_STRING_TOKEN_BY_IDX(), but uppercased.
 *
 * This removes "the quotes" and capitalizes an element in the array, and
 * converts non-alphanumeric characters to underscores. That can be useful, for
 * example, when programmatically using the value to form a C variable or code.
 *
 * DT_STRING_UPPER_TOKEN_BY_IDX() can only be used for properties with
 * string-array type.
 *
 * It is an error to use DT_STRING_UPPER_TOKEN_BY_IDX() in other circumstances.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     n1: node-1 {
 *             prop = "f1", "F2";
 *     };
 *     n2: node-2 {
 *             prop = "123 foo", "456 FOO";
 *     };
 * @endcode
 *
 * Example bindings fragment:
 *
 * @code{.yaml}
 *     properties:
 *       prop:
 *         type: string-array
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_STRING_UPPER_TOKEN_BY_IDX(DT_NODELABEL(n1), prop, 0) // F1
 *     DT_STRING_UPPER_TOKEN_BY_IDX(DT_NODELABEL(n1), prop, 1) // F2
 *     DT_STRING_UPPER_TOKEN_BY_IDX(DT_NODELABEL(n2), prop, 0) // 123_FOO
 *     DT_STRING_UPPER_TOKEN_BY_IDX(DT_NODELABEL(n2), prop, 1) // 456_FOO
 * @endcode
 *
 * For more information, see @ref DT_STRING_UPPER_TOKEN.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param idx the index to get
 * @return the element in @p prop at index @p idx as an uppercased token
 */
#define DT_STRING_UPPER_TOKEN_BY_IDX(node_id, prop, idx) \
	DT_CAT6(node_id, _P_, prop, _IDX_, idx, _STRING_UPPER_TOKEN)

/**
 * @brief Get a string array item value as an unquoted sequence of tokens.
 *
 * This removes "the quotes" from string-valued item.
 * That can be useful, for example,
 * when defining floating point values as a string in devicetree
 * that you would like to use to initialize a float or double variable in C.
 *
 * DT_STRING_UNQUOTED_BY_IDX() can only be used for properties with
 * string-array type.
 *
 * It is an error to use DT_STRING_UNQUOTED_BY_IDX() in other circumstances.
 *
 * Example devicetree fragment:
 *
 *     n1: node-1 {
 *             prop = "12.7", "34.1";
 *     };
 *     n2: node-2 {
 *             prop = "A B", "C D";
 *     }
 *
 * Example bindings fragment:
 *
 *     properties:
 *       prop:
 *         type: string-array
 *
 * Example usage:
 *
 *     DT_STRING_UNQUOTED_BY_IDX(DT_NODELABEL(n1), prop, 0) // 12.7
 *     DT_STRING_UNQUOTED_BY_IDX(DT_NODELABEL(n1), prop, 1) // 34.1
 *     DT_STRING_UNQUOTED_BY_IDX(DT_NODELABEL(n2), prop, 0) // A B
 *     DT_STRING_UNQUOTED_BY_IDX(DT_NODELABEL(n2), prop, 1) // C D
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name
 * @param idx the index to get
 * @return the property's value as a sequence of tokens, with no quotes
 */
#define DT_STRING_UNQUOTED_BY_IDX(node_id, prop, idx) \
	DT_CAT6(node_id, _P_, prop, _IDX_, idx, _STRING_UNQUOTED)

/*
 * phandle properties
 *
 * These are special-cased to manage the impedance mismatch between
 * phandles, which are just uint32_t node properties that only make sense
 * within the tree itself, and C values.
 */

/**
 * @brief Get a property value from a phandle in a property.
 *
 * This is a shorthand for:
 *
 * @code{.c}
 *     DT_PROP(DT_PHANDLE_BY_IDX(node_id, phs, idx), prop)
 * @endcode
 *
 * That is, @p prop is a property of the phandle's node, not a
 * property of @p node_id.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     n1: node-1 {
 *             foo = <&n2 &n3>;
 *     };
 *
 *     n2: node-2 {
 *             bar = <42>;
 *     };
 *
 *     n3: node-3 {
 *             baz = <43>;
 *     };
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     #define N1 DT_NODELABEL(n1)
 *
 *     DT_PROP_BY_PHANDLE_IDX(N1, foo, 0, bar) // 42
 *     DT_PROP_BY_PHANDLE_IDX(N1, foo, 1, baz) // 43
 * @endcode
 *
 * @param node_id node identifier
 * @param phs lowercase-and-underscores property with type `phandle`,
 *            `phandles`, or `phandle-array`
 * @param idx logical index into @p phs, which must be zero if @p phs
 *            has type `phandle`
 * @param prop lowercase-and-underscores property of the phandle's node
 * @return the property's value
 */
#define DT_PROP_BY_PHANDLE_IDX(node_id, phs, idx, prop) \
	DT_PROP(DT_PHANDLE_BY_IDX(node_id, phs, idx), prop)

/**
 * @brief Like DT_PROP_BY_PHANDLE_IDX(), but with a fallback to
 * @p default_value.
 *
 * If the value exists, this expands to DT_PROP_BY_PHANDLE_IDX(node_id, phs,
 * idx, prop). The @p default_value parameter is not expanded in this
 * case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @param node_id node identifier
 * @param phs lowercase-and-underscores property with type `phandle`,
 *            `phandles`, or `phandle-array`
 * @param idx logical index into @p phs, which must be zero if @p phs
 *            has type `phandle`
 * @param prop lowercase-and-underscores property of the phandle's node
 * @param default_value a fallback value to expand to
 * @return the property's value
 */
#define DT_PROP_BY_PHANDLE_IDX_OR(node_id, phs, idx, prop, default_value) \
	DT_PROP_OR(DT_PHANDLE_BY_IDX(node_id, phs, idx), prop, default_value)

/**
 * @brief Get a property value from a phandle's node
 *
 * This is equivalent to DT_PROP_BY_PHANDLE_IDX(node_id, ph, 0, prop).
 *
 * @param node_id node identifier
 * @param ph lowercase-and-underscores property of @p node_id
 *           with type `phandle`
 * @param prop lowercase-and-underscores property of the phandle's node
 * @return the property's value
 */
#define DT_PROP_BY_PHANDLE(node_id, ph, prop) \
	DT_PROP_BY_PHANDLE_IDX(node_id, ph, 0, prop)

/**
 * @brief Get a phandle-array specifier cell value at an index
 *
 * It might help to read the argument order as being similar to
 * `node->phandle_array[index].cell`. That is, the cell value is in
 * the @p pha property of @p node_id, inside the specifier at index
 * @p idx.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     gpio0: gpio@abcd1234 {
 *             #gpio-cells = <2>;
 *     };
 *
 *     gpio1: gpio@1234abcd {
 *             #gpio-cells = <2>;
 *     };
 *
 *     led: led_0 {
 *             gpios = <&gpio0 17 0x1>, <&gpio1 5 0x3>;
 *     };
 * @endcode
 *
 * Bindings fragment for the `gpio0` and `gpio1` nodes:
 *
 * @code{.yaml}
 *     gpio-cells:
 *       - pin
 *       - flags
 * @endcode
 *
 * Above, `gpios` has two elements:
 *
 * - index 0 has specifier <17 0x1>, so its `pin` cell is 17, and its
 *   `flags` cell is 0x1
 * - index 1 has specifier <5 0x3>, so `pin` is 5 and `flags` is 0x3
 *
 * Example usage:
 *
 * @code{.c}
 *     #define LED DT_NODELABEL(led)
 *
 *     DT_PHA_BY_IDX(LED, gpios, 0, pin)   // 17
 *     DT_PHA_BY_IDX(LED, gpios, 1, flags) // 0x3
 * @endcode
 *
 * @param node_id node identifier
 * @param pha lowercase-and-underscores property with type `phandle-array`
 * @param idx logical index into @p pha
 * @param cell lowercase-and-underscores cell name within the specifier
 *             at @p pha index @p idx
 * @return the cell's value
 */
#define DT_PHA_BY_IDX(node_id, pha, idx, cell) \
	DT_CAT7(node_id, _P_, pha, _IDX_, idx, _VAL_, cell)

/**
 * @brief Like DT_PHA_BY_IDX(), but with a fallback to @p default_value.
 *
 * If the value exists, this expands to DT_PHA_BY_IDX(node_id, pha,
 * idx, cell). The @p default_value parameter is not expanded in this
 * case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @internal
 * Implementation note: the _IDX_##idx##_VAL_##cell##_EXISTS macros are
 * defined, so it's safe to use DT_PROP_OR() here, because that uses an
 * IS_ENABLED() on the _EXISTS macro.
 * @endinternal
 *
 * @param node_id node identifier
 * @param pha lowercase-and-underscores property with type `phandle-array`
 * @param idx logical index into @p pha
 * @param cell lowercase-and-underscores cell name within the specifier
 *             at @p pha index @p idx
 * @param default_value a fallback value to expand to
 * @return the cell's value or @p default_value
 */
#define DT_PHA_BY_IDX_OR(node_id, pha, idx, cell, default_value) \
	DT_PROP_OR(node_id, DT_CAT5(pha, _IDX_, idx, _VAL_, cell), default_value)

/**
 * @brief Equivalent to DT_PHA_BY_IDX(node_id, pha, 0, cell)
 * @param node_id node identifier
 * @param pha lowercase-and-underscores property with type `phandle-array`
 * @param cell lowercase-and-underscores cell name
 * @return the cell's value
 */
#define DT_PHA(node_id, pha, cell) DT_PHA_BY_IDX(node_id, pha, 0, cell)

/**
 * @brief Like DT_PHA(), but with a fallback to @p default_value
 *
 * If the value exists, this expands to DT_PHA(node_id, pha, cell).
 * The @p default_value parameter is not expanded in this case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @param node_id node identifier
 * @param pha lowercase-and-underscores property with type `phandle-array`
 * @param cell lowercase-and-underscores cell name
 * @param default_value a fallback value to expand to
 * @return the cell's value or @p default_value
 */
#define DT_PHA_OR(node_id, pha, cell, default_value) \
	DT_PHA_BY_IDX_OR(node_id, pha, 0, cell, default_value)

/**
 * @brief Get a value within a phandle-array specifier by name
 *
 * This is like DT_PHA_BY_IDX(), except it treats @p pha as a structure
 * where each array element has a name.
 *
 * It might help to read the argument order as being similar to
 * `node->phandle_struct.name.cell`. That is, the cell value is in the
 * @p pha property of @p node_id, treated as a data structure where
 * each array element has a name.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     n: node {
 *             io-channels = <&adc1 10>, <&adc2 20>;
 *             io-channel-names = "SENSOR", "BANDGAP";
 *     };
 * @endcode
 *
 * Bindings fragment for the "adc1" and "adc2" nodes:
 *
 * @code{.yaml}
 *     io-channel-cells:
 *       - input
 * @endcode
 *
 * Example usage:
 *
 * @code{.c}
 *     DT_PHA_BY_NAME(DT_NODELABEL(n), io_channels, sensor, input)  // 10
 *     DT_PHA_BY_NAME(DT_NODELABEL(n), io_channels, bandgap, input) // 20
 * @endcode
 *
 * @param node_id node identifier
 * @param pha lowercase-and-underscores property with type `phandle-array`
 * @param name lowercase-and-underscores name of a specifier in @p pha
 * @param cell lowercase-and-underscores cell name in the named specifier
 * @return the cell's value
 */
#define DT_PHA_BY_NAME(node_id, pha, name, cell) \
	DT_CAT7(node_id, _P_, pha, _NAME_, name, _VAL_, cell)

/**
 * @brief Like DT_PHA_BY_NAME(), but with a fallback to @p default_value
 *
 * If the value exists, this expands to DT_PHA_BY_NAME(node_id, pha,
 * name, cell). The @p default_value parameter is not expanded in this case.
 *
 * Otherwise, this expands to @p default_value.
 *
 * @internal
 * Implementation note: the `_NAME_##name##_VAL_##cell##_EXISTS` macros are
 * defined, so it's safe to use DT_PROP_OR() here, because that uses an
 * IS_ENABLED() on the `_EXISTS` macro.
 * @endinternal
 *
 * @param node_id node identifier
 * @param pha lowercase-and-underscores property with type `phandle-array`
 * @param name lowercase-and-underscores name of a specifier in @p pha
 * @param cell lowercase-and-underscores cell name in the named specifier
 * @param default_value a fallback value to expand to
 * @return the cell's value or @p default_value
 */
#define DT_PHA_BY_NAME_OR(node_id, pha, name, cell, default_value) \
	DT_PROP_OR(node_id, DT_CAT5(pha, _NAME_, name, _VAL_, cell), default_value)

/**
 * @brief Get a phandle's node identifier from a phandle array by @p name
 *
 * It might help to read the argument order as being similar to
 * `node->phandle_struct.name.phandle`. That is, the phandle array is
 * treated as a structure with named elements. The return value is
 * the node identifier for a phandle inside the structure.
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     adc1: adc@abcd1234 {
 *             foobar = "ADC_1";
 *     };
 *
 *     adc2: adc@1234abcd {
 *             foobar = "ADC_2";
 *     };
 *
 *     n: node {
 *             io-channels = <&adc1 10>, <&adc2 20>;
 *             io-channel-names = "SENSOR", "BANDGAP";
 *     };
 * @endcode
 *
 * Above, "io-channels" has two elements:
 *
 * - the element named `"SENSOR"` has phandle `&adc1`
 * - the element named `"BANDGAP"` has phandle `&adc2`
 *
 * Example usage:
 *
 * @code{.c}
 *     #define NODE DT_NODELABEL(n)
 *
 *     DT_PROP(DT_PHANDLE_BY_NAME(NODE, io_channels, sensor), foobar)  // "ADC_1"
 *     DT_PROP(DT_PHANDLE_BY_NAME(NODE, io_channels, bandgap), foobar) // "ADC_2"
 * @endcode
 *
 * Notice how devicetree properties and names are lowercased, and
 * non-alphanumeric characters are converted to underscores.
 *
 * @param node_id node identifier
 * @param pha lowercase-and-underscores property with type `phandle-array`
 * @param name lowercase-and-underscores name of an element in @p pha
 * @return a node identifier for the node with that phandle
 */
#define DT_PHANDLE_BY_NAME(node_id, pha, name) \
	DT_CAT6(node_id, _P_, pha, _NAME_, name, _PH)

/**
 * @brief Get a node identifier for a phandle in a property.
 *
 * When a node's value at a logical index contains a phandle, this
 * macro returns a node identifier for the node with that phandle.
 *
 * Therefore, if @p prop has type `phandle`, @p idx must be zero. (A
 * `phandle` type is treated as a `phandles` with a fixed length of
 * 1).
 *
 * Example devicetree fragment:
 *
 * @code{.dts}
 *     n1: node-1 {
 *             foo = <&n2 &n3>;
 *     };
 *
 *     n2: node-2 { ... };
 *     n3: node-3 { ... };
 * @endcode
 *
 * Above, `foo` has type phandles and has two elements:
 *
 * - index 0 has phandle `&n2`, which is `node-2`'s phandle
 * - index 1 has phandle `&n3`, which is `node-3`'s phandle
 *
 * Example usage:
 *
 * @code{.c}
 *     #define N1 DT_NODELABEL(n1)
 *
 *     DT_PHANDLE_BY_IDX(N1, foo, 0) // node identifier for node-2
 *     DT_PHANDLE_BY_IDX(N1, foo, 1) // node identifier for node-3
 * @endcode
 *
 * Behavior is analogous for phandle-arrays.
 *
 * @internal
 * Implementation note: using DT_CAT6 above defers concatenation until
 * after expansion of each parameter. This is important when 'idx' is
 * expandable to a number, but it isn't one "yet".
 * @endinternal
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property name in @p node_id
 *             with type `phandle`, `phandles` or `phandle-array`
 * @param idx index into @p prop
 * @return node identifier for the node with the phandle at that index
 */
#define DT_PHANDLE_BY_IDX(node_id, prop, idx) \
	DT_CAT6(node_id, _P_, prop, _IDX_, idx, _PH)

/**
 * @brief Get a node identifier for a phandle property's value
 *
 * This is equivalent to DT_PHANDLE_BY_IDX(node_id, prop, 0). Its primary
 * benefit is readability when @p prop has type `phandle`.
 *
 * @param node_id node identifier
 * @param prop lowercase-and-underscores property of @p node_id
 *             with type `phandle`
 * @return a node identifier for the node pointed to by "ph"
 */
#define DT_PHANDLE(node_id, prop) DT_PHANDLE_BY_IDX(node_id, prop, 0)

/**
 * @}
 */

/**
