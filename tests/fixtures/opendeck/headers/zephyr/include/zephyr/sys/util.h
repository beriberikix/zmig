/*
 * Copyright (c) 2011-2014, Wind River Systems, Inc.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief Misc utilities
 *
 * Misc utilities usable by the kernel and application code.
 */

#ifndef ZEPHYR_INCLUDE_SYS_UTIL_H_
#define ZEPHYR_INCLUDE_SYS_UTIL_H_

#include <zephyr/sys/util_macro.h>
#include <zephyr/toolchain.h>

/* needs to be outside _ASMLANGUAGE so 'true' and 'false' can turn
 * into '1' and '0' for asm or linker scripts
 */
#include <stdbool.h>

#ifndef _ASMLANGUAGE

#include <zephyr/sys/__assert.h>
#include <zephyr/types.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/** @brief Number of bits that make up a type */
#define NUM_BITS(t) (sizeof(t) * BITS_PER_BYTE)

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @defgroup sys-util Utility Functions
 * @since 2.4
 * @version 0.1.0
 * @ingroup utilities
 * @{
 */

/** @brief Cast @p x, a pointer, to an unsigned integer. */
#define POINTER_TO_UINT(x) ((uintptr_t)(x))
/** @brief Cast @p x, an unsigned integer, to a <tt>void*</tt>. */
#define UINT_TO_POINTER(x) ((void *)(uintptr_t)(x))
/** @brief Cast @p x, a pointer, to a signed integer. */
#define POINTER_TO_INT(x)  ((intptr_t)(x))
/** @brief Cast @p x, a signed integer, to a <tt>void*</tt>. */
#define INT_TO_POINTER(x)  ((void *)(intptr_t)(x))

#if !(defined(__CHAR_BIT__) && defined(__SIZEOF_LONG__) && defined(__SIZEOF_LONG_LONG__))
#error Missing required predefined macros for BITS_PER_LONG calculation
#endif

/** Number of bits in a byte. */
#define BITS_PER_BYTE (__CHAR_BIT__)

/** Number of bits in a nibble. */
#define BITS_PER_NIBBLE (__CHAR_BIT__ / 2)

/** Number of nibbles in a byte. */
#define NIBBLES_PER_BYTE (BITS_PER_BYTE / BITS_PER_NIBBLE)

/** Number of bits in a long int. */
#define BITS_PER_LONG (__CHAR_BIT__ * __SIZEOF_LONG__)

/** Number of bits in a long long int. */
#define BITS_PER_LONG_LONG (__CHAR_BIT__ * __SIZEOF_LONG_LONG__)

/**
 * @brief Create a contiguous bitmask starting at bit position @p l
 *        and ending at position @p h.
 */
#define GENMASK(h, l) (((~0UL) - (1UL << (l)) + 1) & (~0UL >> (BITS_PER_LONG - 1 - (h))))

/**
 * @brief Create a contiguous 64-bit bitmask starting at bit position @p l
 *        and ending at position @p h.
 */
#define GENMASK64(h, l) (((~0ULL) - (1ULL << (l)) + 1) & (~0ULL >> (BITS_PER_LONG_LONG - 1 - (h))))

/** @brief 0 if @p cond is true-ish; causes a compile error otherwise. */
#define ZERO_OR_COMPILE_ERROR(cond) ((int)sizeof(char[1 - (2 * !(cond))]) - 1)

#if defined(__cplusplus)

/* The built-in function used below for type checking in C is not
 * supported by GNU C++.
 */
#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))

#else /* __cplusplus */

/**
 * @brief Zero if @p array has an array type, a compile error otherwise
 *
 * This macro is available only from C, not C++.
 */
#define IS_ARRAY(array)                                                                            \
	ZERO_OR_COMPILE_ERROR(                                                                     \
		!__builtin_types_compatible_p(__typeof__(array), __typeof__(&(array)[0])))

/**
 * @brief Number of elements in the given @p array
 *
 * In C++, due to language limitations, this will accept as @p array
 * any type that implements <tt>operator[]</tt>. The results may not be
 * particularly meaningful in this case.
 *
 * In C, passing a pointer as @p array causes a compile error.
 */
#define ARRAY_SIZE(array) ((size_t)(IS_ARRAY(array) + (sizeof(array) / sizeof((array)[0]))))

#endif /* __cplusplus */

/**
 * @brief Declare a flexible array member.
 *
 * This macro declares a flexible array member in a struct. The member
 * is named @p name and has type @p type.
 *
 * Since C99, flexible arrays are part of the C standard, but for historical
 * reasons many places still use an older GNU extension that is declare
 * zero length arrays.
 *
 * Although zero length arrays are flexible arrays, we can't blindly
 * replace [0] with [] because of some syntax limitations. This macro
 * workaround these limitations.
 *
 * It is specially useful for cases where flexible arrays are
 * used in unions or are not the last element in the struct.
 */
#define FLEXIBLE_ARRAY_DECLARE(type, name)                                                         \
	struct {                                                                                   \
		struct {                                                                           \
		} __unused_##name;                                                                 \
		type name[];                                                                       \
	}

/**
 * @brief Whether @p ptr is an element of @p array
 *
 * This macro can be seen as a slightly stricter version of @ref PART_OF_ARRAY
 * in that it also ensures that @p ptr is aligned to an array-element boundary
 * of @p array.
 *
 * In C, passing a pointer as @p array causes a compile error.
 *
 * @param array the array in question
 * @param ptr the pointer to check
 *
 * @return 1 if @p ptr is part of @p array, 0 otherwise
 */
#define IS_ARRAY_ELEMENT(array, ptr)                                                               \
	((ptr) && POINTER_TO_UINT(array) <= POINTER_TO_UINT(ptr) &&                                \
	 POINTER_TO_UINT(ptr) < POINTER_TO_UINT(&(array)[ARRAY_SIZE(array)]) &&                    \
	 (POINTER_TO_UINT(ptr) - POINTER_TO_UINT(array)) % sizeof((array)[0]) == 0)

/**
 * @brief Index of @p ptr within @p array
 *
 * With `CONFIG_ASSERT=y`, this macro will trigger a runtime assertion
 * when @p ptr does not fall into the range of @p array or when @p ptr
 * is not aligned to an array-element boundary of @p array.
 *
 * In C, passing a pointer as @p array causes a compile error.
 *
 * @param array the array in question
 * @param ptr pointer to an element of @p array
 *
 * @return the array index of @p ptr within @p array, on success
 */
#define ARRAY_INDEX(array, ptr)                                                                    \
	({                                                                                         \
		__ASSERT_NO_MSG(IS_ARRAY_ELEMENT(array, ptr));                                     \
		(__typeof__((array)[0]) *)(ptr) - (array);                                         \
	})

/**
 * @brief Check if a pointer @p ptr lies within @p array.
 *
 * In C but not C++, this causes a compile error if @p array is not an array
 * (e.g. if @p ptr and @p array are mixed up).
 *
 * @param array an array
 * @param ptr a pointer
 * @return 1 if @p ptr is part of @p array, 0 otherwise
 */
#define PART_OF_ARRAY(array, ptr)                                                                  \
	((ptr) && POINTER_TO_UINT(array) <= POINTER_TO_UINT(ptr) &&                                \
	 POINTER_TO_UINT(ptr) < POINTER_TO_UINT(&(array)[ARRAY_SIZE(array)]))

/**
 * @brief Array-index of @p ptr within @p array, rounded down
 *
 * This macro behaves much like @ref ARRAY_INDEX with the notable
 * difference that it accepts any @p ptr in the range of @p array rather than
 * exclusively a @p ptr aligned to an array-element boundary of @p array.
 *
 * With `CONFIG_ASSERT=y`, this macro will trigger a runtime assertion
 * when @p ptr does not fall into the range of @p array.
 *
 * In C, passing a pointer as @p array causes a compile error.
 *
 * @param array the array in question
 * @param ptr pointer to an element of @p array
 *
 * @return the array index of @p ptr within @p array, on success
 */
#define ARRAY_INDEX_FLOOR(array, ptr)                                                              \
	({                                                                                         \
		__ASSERT_NO_MSG(PART_OF_ARRAY(array, ptr));                                        \
		(POINTER_TO_UINT(ptr) - POINTER_TO_UINT(array)) / sizeof((array)[0]);              \
	})

/**
 * @brief Iterate over members of an array using an index variable
 *
 * @param array the array in question
 * @param idx name of array index variable
 */
#define ARRAY_FOR_EACH(array, idx) for (size_t idx = 0; (idx) < ARRAY_SIZE(array); ++(idx))

/**
 * @brief Iterate over members of an array using a pointer
 *
 * @param array the array in question
 * @param ptr pointer to an element of @p array
 */
#define ARRAY_FOR_EACH_PTR(array, ptr)                                                             \
	for (__typeof__(*(array)) *ptr = (array); (size_t)((ptr) - (array)) < ARRAY_SIZE(array);   \
	     ++(ptr))

/**
 * @brief Validate if two entities have a compatible type
 *
 * @param a the first entity to be compared
 * @param b the second entity to be compared
 * @return 1 if the two elements are compatible, 0 if they are not
 */
#define SAME_TYPE(a, b) __builtin_types_compatible_p(__typeof__(a), __typeof__(b))

/**
 * @brief Validate CONTAINER_OF parameters, only applies to C mode.
 */
#ifndef __cplusplus
#define CONTAINER_OF_VALIDATE(ptr, type, field)                                                    \
	BUILD_ASSERT(SAME_TYPE(*(ptr), ((type *)0)->field) || SAME_TYPE(*(ptr), void),             \
		     "pointer type mismatch in CONTAINER_OF");
#else
#define CONTAINER_OF_VALIDATE(ptr, type, field)
#endif

/**
 * @brief Get a pointer to a structure containing the element
 *
 * Example:
 *
 *	struct foo {
 *		int bar;
 *	};
 *
 *	struct foo my_foo;
 *	int *ptr = &my_foo.bar;
 *
 *	struct foo *container = CONTAINER_OF(ptr, struct foo, bar);
 *
 * Above, @p container points at @p my_foo.
 *
 * @param ptr pointer to a structure element
 * @param type name of the type that @p ptr is an element of
 * @param field the name of the field within the struct @p ptr points to
 * @return a pointer to the structure that contains @p ptr
 */
#define CONTAINER_OF(ptr, type, field)                                                             \
	({                                                                                         \
		CONTAINER_OF_VALIDATE(ptr, type, field)                                            \
		((type *)(((char *)(ptr)) - offsetof(type, field)));                               \
	})

/**
