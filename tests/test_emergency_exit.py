import pytest
from brownie import Contract, chain
from utils import harvest_strategy, check_status

# test that emergency exit works properly
def test_emergency_exit(
    gov,
    token,
    vault,
    whale,
    strategy,
    amount,
    is_slippery,
    no_profit,
    sleep_time,
    profit_whale,
    profit_amount,
    destination_strategy,
    use_yswaps,
    RELATIVE_APPROX,
):
    ## deposit to the vault after approving
    starting_whale = token.balanceOf(whale)
    token.approve(vault, 2**256 - 1, {"from": whale})
    vault.deposit(amount, {"from": whale})
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter first harvest")
    strategy_params = check_status(strategy, vault)

    # evaluate our current total assets
    old_assets = vault.totalAssets()
    initial_strategy_assets = strategy.estimatedTotalAssets()
    initial_debt = strategy_params["totalDebt"]
    starting_share_price = vault.pricePerShare()

    # simulate earnings
    chain.sleep(sleep_time)
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nBefore exit, after second harvest")
    strategy_params = check_status(strategy, vault)

    # yswaps will not have taken this first batch of profit yet
    if use_yswaps:
        assert strategy_params["totalGain"] == 0
    else:
        assert strategy_params["totalGain"] > 0

    # set emergency and exit, then confirm that the strategy has no funds
    strategy.setEmergencyExit({"from": gov})

    # check our current status
    print("\nAfter exit + before third harvest")
    strategy_params = check_status(strategy, vault)

    # debtOutstanding should be the entire debt, DR and credit should be zero
    assert vault.debtOutstanding(strategy) == strategy_params["totalDebt"] > 0
    assert vault.creditAvailable(strategy) == 0
    assert strategy_params["debtRatio"] == 0

    # harvest to send funds back to vault
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter third harvest")
    strategy_params = check_status(strategy, vault)

    # yswaps should have finally taken our first round of profit
    assert strategy_params["totalGain"] > 0

    # debtOutstanding, debt, credit should now be zero, but we will still send any earned profits immediately back to vault
    assert (
        vault.debtOutstanding(strategy)
        == strategy_params["totalDebt"]
        == vault.creditAvailable(strategy)
        == 0
    )

    # yswaps needs another harvest to get the final bit of profit to the vault
    if use_yswaps:
        old_gain = strategy_params["totalGain"]
        (profit, loss) = harvest_strategy(
            use_yswaps,
            strategy,
            token,
            gov,
            profit_whale,
            profit_amount,
            destination_strategy,
        )

        # check our current status
        print("\nAfter yswaps extra harvest")
        strategy_params = check_status(strategy, vault)

        # make sure we recorded our gain properly
        if not no_profit:
            assert strategy_params["totalGain"] > old_gain

    # strategy should be completely empty now, even if no profit or slippery
    assert strategy.estimatedTotalAssets() == 0

    # simulate 5 days of waiting for share price to bump back up
    chain.sleep(86400 * 5)
    chain.mine(1)

    # check our current status
    print("\nAfter sleep for share price")
    strategy_params = check_status(strategy, vault)

    # share price should have gone up, without loss except for special cases
    if is_slippery and no_profit:
        assert (
            pytest.approx(vault.pricePerShare(), rel=RELATIVE_APPROX)
            == starting_share_price
        )
    else:
        assert vault.pricePerShare() > starting_share_price
        assert strategy_params["totalLoss"] == 0

    # withdraw and confirm we made money, or at least that we have about the same (profit whale has to be different from normal whale)
    vault.withdraw({"from": whale})
    if is_slippery and no_profit:
        assert (
            pytest.approx(token.balanceOf(whale), rel=RELATIVE_APPROX) == starting_whale
        )
    else:
        assert token.balanceOf(whale) >= starting_whale


# test emergency exit, but with a donation (profit)
def test_emergency_exit_with_profit(
    gov,
    token,
    vault,
    whale,
    strategy,
    amount,
    is_slippery,
    no_profit,
    sleep_time,
    profit_whale,
    profit_amount,
    destination_strategy,
    use_yswaps,
    RELATIVE_APPROX,
):
    ## deposit to the vault after approving
    starting_whale = token.balanceOf(whale)
    token.approve(vault, 2**256 - 1, {"from": whale})
    vault.deposit(amount, {"from": whale})
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter first harvest")
    strategy_params = check_status(strategy, vault)

    # evaluate our current total assets
    old_assets = vault.totalAssets()
    initial_strategy_assets = strategy.estimatedTotalAssets()
    initial_debt = strategy_params["totalDebt"]
    starting_share_price = vault.pricePerShare()

    # simulate earnings
    chain.sleep(sleep_time)
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter second harvest")
    strategy_params = check_status(strategy, vault)

    # yswaps will not have taken this first batch of profit yet
    if use_yswaps:
        assert strategy_params["totalGain"] == 0
    else:
        assert strategy_params["totalGain"] > 0

    # turn off health check since this will be a big profit from the donation
    donation = amount / 2
    token.transfer(strategy, donation, {"from": profit_whale})
    strategy.setDoHealthCheck(False, {"from": gov})

    # check our current status
    print("\nBefore exit, after donation")
    strategy_params = check_status(strategy, vault)

    # we should have more assets but the same debt
    assert strategy.estimatedTotalAssets() > initial_strategy_assets
    assert strategy_params["totalDebt"] == initial_debt

    # set emergency and exit
    strategy.setEmergencyExit({"from": gov})

    # check our current status
    print("\nAfter exit + before third harvest")
    strategy_params = check_status(strategy, vault)

    # debtOutstanding uses both totalAssets and totalDebt
    # with 10_000 DR they should all be the same (since we haven't taken donation as profit yet)
    assert (
        strategy_params["totalDebt"]
        == initial_debt
        == old_assets
        == vault.debtOutstanding(strategy)
    )

    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter third harvest")
    strategy_params = check_status(strategy, vault)

    # yswaps should have finally taken our first round of profit
    assert strategy_params["totalGain"] > 0

    # debtOutstanding, debt, credit should now be zero, but we will still send any earned profits immediately back to vault
    assert (
        vault.debtOutstanding(strategy)
        == strategy_params["totalDebt"]
        == vault.creditAvailable(strategy)
        == 0
    )

    # yswaps needs another harvest to get the final bit of profit to the vault
    if use_yswaps:
        old_gain = strategy_params["totalGain"]
        (profit, loss) = harvest_strategy(
            use_yswaps,
            strategy,
            token,
            gov,
            profit_whale,
            profit_amount,
            destination_strategy,
        )

        # check our current status
        print("\nAfter yswaps extra harvest")
        strategy_params = check_status(strategy, vault)

        # make sure we recorded our gain properly
        if not no_profit:
            assert strategy_params["totalGain"] > old_gain

    # confirm that the strategy has no funds
    assert strategy.estimatedTotalAssets() == 0

    # simulate 5 days of waiting for share price to bump back up
    chain.sleep(86400 * 5)
    chain.mine(1)

    # check our current status
    print("\nAfter sleep for share price")
    strategy_params = check_status(strategy, vault)

    # share price should have gone up, without loss except for special cases
    if is_slippery and no_profit:
        assert (
            pytest.approx(vault.pricePerShare(), rel=RELATIVE_APPROX)
            == starting_share_price
        )
    else:
        assert vault.pricePerShare() > starting_share_price
        assert strategy_params["totalLoss"] == 0

    # withdraw and confirm we made money, or at least that we have about the same (profit whale has to be different from normal whale)
    vault.withdraw({"from": whale})
    if is_slippery and no_profit:
        assert (
            pytest.approx(token.balanceOf(whale), rel=RELATIVE_APPROX) == starting_whale
        )
    else:
        assert token.balanceOf(whale) >= starting_whale


# test emergency exit, but after somehow losing all of our assets (oopsie)
def test_emergency_exit_with_loss(
    gov,
    token,
    vault,
    whale,
    strategy,
    amount,
    is_slippery,
    no_profit,
    sleep_time,
    profit_whale,
    profit_amount,
    destination_strategy,
    use_yswaps,
    old_vault,
    RELATIVE_APPROX,
):
    ## deposit to the vault after approving
    starting_whale = token.balanceOf(whale)
    token.approve(vault, 2**256 - 1, {"from": whale})
    vault.deposit(amount, {"from": whale})
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nBefore funds loss, after first harvest")
    strategy_params = check_status(strategy, vault)

    # evaluate our current total assets
    old_assets = vault.totalAssets()
    initial_debt = strategy_params["totalDebt"]
    starting_share_price = vault.pricePerShare()
    initial_strategy_assets = strategy.estimatedTotalAssets()

    ################# SEND ALL FUNDS AWAY. ADJUST AS NEEDED PER STRATEGY. #################
    staking = Contract(strategy.lqtyStaking())
    to_send = staking.stakes(strategy)
    staking.unstake(to_send, {"from": strategy})
    token.transfer(gov, to_send, {"from": strategy})

    ################# SET FALSE IF PROFIT EXPECTED. ADJUST AS NEEDED. #################
    # set this true if no profit on this test. it is normal for a strategy to not generate profit here.
    # realistically only wrapped tokens or every-block earners will see profits (convex, etc).
    # also checked in test_change_debt
    no_profit = True

    # check our current status
    print("\nBefore dust transfer, after main fund transfer")
    strategy_params = check_status(strategy, vault)

    # we shouldn't have taken any actual losses yet, and debt/DR/share price should all still be the same
    assert strategy_params["debtRatio"] == 10_000
    assert strategy_params["totalLoss"] == 0
    assert strategy_params["totalDebt"] == initial_debt == old_assets
    assert vault.pricePerShare() == starting_share_price

    # if slippery, then assets may differ slightly from debt
    if is_slippery:
        assert (
            pytest.approx(initial_debt, rel=RELATIVE_APPROX) == initial_strategy_assets
        )
    else:
        assert initial_debt == initial_strategy_assets

    # confirm we emptied the strategy
    assert strategy.estimatedTotalAssets() == 0

    # our whale donates 5 wei to the vault so we don't divide by zero (needed for older vaults)
    if old_vault:
        dust_donation = 5
        token.transfer(strategy, dust_donation, {"from": whale})
        assert strategy.estimatedTotalAssets() == dust_donation

    # check our current status
    print("\nBefore exit, after funds transfer out + dust transfer in")
    strategy_params = check_status(strategy, vault)

    # we shouldn't have taken any actual losses yet, and debt/DR/share price should all still be the same
    assert strategy_params["debtRatio"] == 10_000
    assert strategy_params["totalLoss"] == 0
    assert strategy_params["totalDebt"] == initial_debt == old_assets
    assert vault.pricePerShare() == starting_share_price
    assert vault.debtOutstanding(strategy) == 0

    # set emergency and exit, but turn off health check since we're taking a huge L
    strategy.setEmergencyExit({"from": gov})

    # check our current status
    print("\nAfter exit + before second harvest")
    strategy_params = check_status(strategy, vault)

    # we shouldn't have taken any actual losses yet, only DR and debtOutstanding should have changed
    assert vault.pricePerShare() == starting_share_price
    assert strategy_params["debtRatio"] == 0
    assert strategy_params["totalLoss"] == 0
    assert vault.creditAvailable(strategy) == 0
    assert strategy_params["debtRatio"] == 0

    # debtOutstanding uses both totalAssets and totalDebt, with 10_000 DR they should all be the same
    assert (
        strategy_params["totalDebt"]
        == initial_debt
        == old_assets
        == vault.debtOutstanding(strategy)
    )

    # if slippery, then assets may differ slightly from debt
    if is_slippery:
        assert (
            pytest.approx(initial_debt, rel=RELATIVE_APPROX) == initial_strategy_assets
        )
    else:
        assert initial_debt == initial_strategy_assets

    strategy.setDoHealthCheck(False, {"from": gov})
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter second harvest (losses taken)")
    strategy_params = check_status(strategy, vault)

    # DR goes to zero, loss is > 0, gain and debt should be zero, share price zero (bye-bye assets 💀)
    assert strategy_params["debtRatio"] == 0
    assert strategy_params["totalLoss"] > 0
    assert strategy_params["totalDebt"] == strategy_params["totalGain"] == 0
    assert vault.pricePerShare() == 0

    # yswaps needs another harvest to get the final bit of profit to the vault
    if use_yswaps:
        old_gain = strategy_params["totalGain"]
        (profit, loss) = harvest_strategy(
            use_yswaps,
            strategy,
            token,
            gov,
            profit_whale,
            profit_amount,
            destination_strategy,
        )
        print("Profit:", profit / 1e18)

        # check our current status
        print("\nAfter yswaps extra harvest")
        strategy_params = check_status(strategy, vault)

        # make sure we recorded our gain properly
        if not no_profit:
            assert strategy_params["totalGain"] > old_gain

    # confirm that the strategy has no funds, even for old vaults with the dust donation
    assert strategy.estimatedTotalAssets() == 0

    # vault should also have no assets or just profit, except old ones will also have 5 wei
    expected_assets = 0
    if use_yswaps and not no_profit:
        expected_assets += profit_amount
    if old_vault:
        expected_assets += dust_donation
    assert vault.totalAssets() == expected_assets

    # simulate 5 days of waiting for share price to bump back up
    chain.sleep(86400 * 5)
    chain.mine(1)

    # check our current status
    print("\nAfter sleep for share price")
    strategy_params = check_status(strategy, vault)

    # withdraw and see how down bad we are, confirming we can withdraw from an empty (or mostly empty) vault
    vault.withdraw({"from": whale})
    print(
        "Raw loss:",
        (starting_whale - token.balanceOf(whale)) / 1e18,
        "Percentage:",
        (starting_whale - token.balanceOf(whale)) / starting_whale,
    )
    print("Share price:", vault.pricePerShare() / 1e18)


# test emergency exit, after somehow losing all of our assets but miraculously getting them recovered 🍀
def test_emergency_exit_with_no_loss(
    gov,
    token,
    vault,
    whale,
    strategy,
    amount,
    is_slippery,
    no_profit,
    sleep_time,
    profit_whale,
    profit_amount,
    destination_strategy,
    use_yswaps,
    RELATIVE_APPROX,
):
    ## deposit to the vault after approving
    starting_whale = token.balanceOf(whale)
    token.approve(vault, 2**256 - 1, {"from": whale})
    vault.deposit(amount, {"from": whale})
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter first harvest")
    strategy_params = check_status(strategy, vault)

    # evaluate our current total assets
    old_assets = vault.totalAssets()
    initial_debt = strategy_params["totalDebt"]
    starting_share_price = vault.pricePerShare()
    initial_strategy_assets = strategy.estimatedTotalAssets()

    ################# SEND ALL FUNDS AWAY. ADJUST AS NEEDED PER STRATEGY. #################
    staking = Contract(strategy.lqtyStaking())
    to_send = staking.stakes(strategy)
    staking.unstake(to_send, {"from": strategy})
    token.transfer(gov, to_send, {"from": strategy})

    ################# SET FALSE IF PROFIT EXPECTED. ADJUST AS NEEDED. #################
    # set this true if no profit on this test. it is normal for a strategy to not generate profit here.
    # realistically only wrapped tokens or every-block earners will see profits (convex, etc).
    # also checked in test_change_debt
    no_profit = True

    # check our current status
    print("\nAfter sending funds away")
    strategy_params = check_status(strategy, vault)

    # confirm we emptied the strategy
    assert strategy.estimatedTotalAssets() == 0

    # confirm everything else stayed the same
    assert strategy_params["debtRatio"] == 10_000
    assert strategy_params["totalLoss"] == 0
    assert strategy_params["totalDebt"] == initial_debt == old_assets
    assert vault.pricePerShare() == starting_share_price
    assert vault.debtOutstanding(strategy) == 0

    # gov sends it back
    token.transfer(strategy, to_send, {"from": gov})

    # check our current status
    print("\nAfter getting funds back")
    strategy_params = check_status(strategy, vault)

    # confirm we got our assets back, exactly the same
    assert strategy.estimatedTotalAssets() == initial_strategy_assets

    # confirm everything else stayed the same
    assert strategy_params["debtRatio"] == 10_000
    assert strategy_params["totalLoss"] == 0
    assert strategy_params["totalDebt"] == initial_debt == old_assets
    assert vault.pricePerShare() == starting_share_price
    assert vault.debtOutstanding(strategy) == 0

    # set emergency exit
    strategy.setEmergencyExit({"from": gov})

    # check our current status
    print("\nAfter exit + before second harvest")
    strategy_params = check_status(strategy, vault)

    # only DR and debtOutstanding should have changed
    assert vault.pricePerShare() == starting_share_price
    assert strategy_params["debtRatio"] == 0
    assert strategy_params["totalLoss"] == 0
    assert vault.creditAvailable(strategy) == 0
    assert strategy_params["debtRatio"] == 0

    # debtOutstanding uses both totalAssets and totalDebt, starting from 10_000 DR they should all be the same
    # note that during vault.report(), if DR == 0 or emergencyShutdown is true, then estimatedTotalAssets() is used instead for debtOustanding
    assert (
        strategy_params["totalDebt"]
        == initial_debt
        == old_assets
        == vault.debtOutstanding(strategy)
    )

    # if slippery, then assets may differ slightly from debt
    if is_slippery:
        assert (
            pytest.approx(initial_debt, rel=RELATIVE_APPROX) == initial_strategy_assets
        )
    else:
        assert initial_debt == initial_strategy_assets

    # harvest to send all funds back to the vault
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter second harvest")
    strategy_params = check_status(strategy, vault)

    # DR goes to zero, loss, gain, and debt should be zero.
    assert strategy_params["debtRatio"] == 0
    assert strategy_params["totalDebt"] == strategy_params["totalLoss"] == 0

    # yswaps needs another harvest to get the final bit of profit to the vault
    if use_yswaps:
        old_gain = strategy_params["totalGain"]
        (profit, loss) = harvest_strategy(
            use_yswaps,
            strategy,
            token,
            gov,
            profit_whale,
            profit_amount,
            destination_strategy,
        )
        print("Profit:", profit / 1e18)

        # check our current status
        print("\nAfter yswaps extra harvest")
        strategy_params = check_status(strategy, vault)

        # make sure we recorded our gain properly
        if not no_profit:
            assert strategy_params["totalGain"] > old_gain

    # confirm that the strategy has no funds
    assert strategy.estimatedTotalAssets() == 0

    # debtOutstanding and credit should now be zero, but we will still send any earned profits immediately back to vault
    assert vault.debtOutstanding(strategy) == vault.creditAvailable(strategy) == 0

    # many strategies will still earn some small amount of profit, or even normal profit if we hold our assets as a wrapped yield-bearing token
    if no_profit:
        assert strategy_params["totalGain"] == 0
        assert vault.pricePerShare() == starting_share_price
        assert vault.totalAssets() == old_assets
    else:
        assert strategy_params["totalGain"] > 0
        assert vault.pricePerShare() > starting_share_price
        assert vault.totalAssets() > old_assets

    # confirm we didn't lose anything, or at worst just dust
    if is_slippery and no_profit:
        assert pytest.approx(loss, rel=RELATIVE_APPROX) == 0
    else:
        assert loss == 0

    # simulate 5 days of waiting for share price to bump back up
    chain.sleep(86400 * 5)
    chain.mine(1)

    # check our current status
    print("\nAfter sleep for share price")
    strategy_params = check_status(strategy, vault)

    # share price should have gone up, without loss except for special cases
    if no_profit:
        assert (
            pytest.approx(vault.pricePerShare(), rel=RELATIVE_APPROX)
            == starting_share_price
        )
    else:
        assert vault.pricePerShare() > starting_share_price
        assert strategy_params["totalLoss"] == 0

    # withdraw and confirm we made money, or at least that we have about the same
    vault.withdraw({"from": whale})
    if no_profit or use_yswaps or no_profit_here:
        assert (
            pytest.approx(token.balanceOf(whale), rel=RELATIVE_APPROX) == starting_whale
        )
    else:
        assert token.balanceOf(whale) > starting_whale


# test calling emergency shutdown from the vault, harvesting to ensure we can get all assets out
def test_emergency_shutdown_from_vault(
    gov,
    token,
    vault,
    whale,
    strategy,
    chain,
    amount,
    sleep_time,
    is_slippery,
    no_profit,
    profit_whale,
    profit_amount,
    destination_strategy,
    use_yswaps,
    RELATIVE_APPROX,
):
    ## deposit to the vault after approving
    starting_whale = token.balanceOf(whale)
    token.approve(vault, 2**256 - 1, {"from": whale})
    vault.deposit(amount, {"from": whale})
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter first harvest")
    strategy_params = check_status(strategy, vault)

    # evaluate our current total assets
    old_assets = vault.totalAssets()
    initial_debt = strategy_params["totalDebt"]
    starting_share_price = vault.pricePerShare()
    initial_strategy_assets = strategy.estimatedTotalAssets()

    # simulate earnings
    chain.sleep(sleep_time)
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # simulate earnings
    chain.sleep(sleep_time)

    # check our current status
    print("\nAfter second harvest, before emergency shutdown")
    strategy_params = check_status(strategy, vault)

    # yswaps will not have taken this first batch of profit yet. this profit is also credit available.
    if use_yswaps:
        assert strategy_params["totalGain"] == 0
        assert vault.creditAvailable(strategy) == 0
    else:
        assert strategy_params["totalGain"] > 0
        assert vault.creditAvailable(strategy) > 0

    # set emergency shutdown, then confirm that the strategy has no funds
    # in emergency shutdown deposits are closed, strategies can't be added, all debt
    #  is outstanding, credit is zero, and asset for all strategy assets during report
    vault.setEmergencyShutdown(True, {"from": gov})

    # check our current status
    print("\nAfter shutdown + before third harvest")
    strategy_params = check_status(strategy, vault)

    # debtOutstanding should be the entire debt. this will also equal our initial debt as our first profit is still in the vault
    # credit available should be zero, but DR is unaffected
    assert (
        vault.debtOutstanding(strategy) == strategy_params["totalDebt"] == initial_debt
    )
    assert vault.creditAvailable(strategy) == 0
    assert strategy_params["debtRatio"] == 10_000

    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check our current status
    print("\nAfter third harvest")
    strategy_params = check_status(strategy, vault)

    # yswaps should have finally taken our first round of profit
    assert strategy_params["totalGain"] > 0

    # debtOutstanding, debt, credit should now be zero, but we will still send any earned profits immediately back to vault
    assert (
        vault.debtOutstanding(strategy)
        == strategy_params["totalDebt"]
        == vault.creditAvailable(strategy)
        == 0
    )

    # harvest again to get the last of our profit with ySwaps
    if use_yswaps:
        old_gain = strategy_params["totalGain"]
        (profit, loss) = harvest_strategy(
            use_yswaps,
            strategy,
            token,
            gov,
            profit_whale,
            profit_amount,
            destination_strategy,
        )

        # check our current status
        print("\nAfter yswaps extra harvest")
        strategy_params = check_status(strategy, vault)

        # make sure we recorded our gain properly
        if not no_profit:
            assert strategy_params["totalGain"] > old_gain

    # shouldn't have any assets, unless we have slippage, then this might leave dust
    # for complete emptying, use emergencyExit
    if is_slippery:
        assert pytest.approx(strategy.estimatedTotalAssets(), rel=RELATIVE_APPROX) == 0
    else:
        assert strategy.estimatedTotalAssets() == 0

    # confirm we didn't lose anything, or at worst just dust
    if is_slippery and no_profit:
        assert pytest.approx(loss, rel=RELATIVE_APPROX) == 0
    else:
        assert loss == 0

    # simulate 5 days of waiting for share price to bump back up
    chain.sleep(86400 * 5)
    chain.mine(1)

    # check our current status
    print("\nAfter sleep for share price")
    strategy_params = check_status(strategy, vault)

    # share price should have gone up, without loss except for special cases
    if is_slippery and no_profit:
        assert (
            pytest.approx(vault.pricePerShare(), rel=RELATIVE_APPROX)
            == starting_share_price
        )
    else:
        assert vault.pricePerShare() > starting_share_price
        assert strategy_params["totalLoss"] == 0

    # withdraw and confirm we made money, or at least that we have about the same (profit whale has to be different from normal whale)
    vault.withdraw({"from": whale})
    if is_slippery and no_profit:
        assert (
            pytest.approx(token.balanceOf(whale), rel=RELATIVE_APPROX) == starting_whale
        )
    else:
        assert token.balanceOf(whale) >= starting_whale
