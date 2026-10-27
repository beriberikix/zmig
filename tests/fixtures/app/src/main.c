#include <zephyr/kernel.h>

int main(void)
{
	printk("zmig fixture on %s\n", CONFIG_BOARD);
	return 0;
}
