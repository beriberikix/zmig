#include <zephyr/kernel.h>

int main(void)
{
	printk("zmig cross fixture on %s\n", CONFIG_BOARD_TARGET);
	return 0;
}
