import brownie
from brownie import Contract, accounts, chain, interface
import pytest
from utils import harvest_strategy

# this test module is specific to this strategy; other protocols may require similar extra contracts and/or testing
# test the our strategy's ability to deposit, harvest, and withdraw, with different optimal deposit tokens if we have them
# turn on keepLQTY for this version
def test_simple_harvest_keep(
    gov,
    token,
    vault,
    whale,
    strategy,
    amount,
    sleep_time,
    is_slippery,
    no_profit,
    profit_whale,
    profit_amount,
    destination_strategy,
    use_yswaps,
    booster,
):
    ## deposit to the vault after approving
    starting_whale = token.balanceOf(whale)
    token.approve(vault, 2**256 - 1, {"from": whale})
    vault.deposit(amount, {"from": whale})
    newWhale = token.balanceOf(whale)

    # harvest, store asset amount
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )
    old_assets = vault.totalAssets()
    assert old_assets > 0
    assert token.balanceOf(strategy) == 0
    assert strategy.estimatedTotalAssets() > 0

    # turn on keeping some LQTY for our booster
    strategy.setBooster(booster, {"from": gov})

    # simulate profits
    chain.sleep(sleep_time)

    # check our name for fun (jk, for coverage)
    name = booster.name()
    print("Name:", name)

    # re-set strategy
    booster.setStrategy(strategy, {"from": gov})

    # harvest, store new asset amount
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # need second harvest to get some profits sent to booster (ySwaps)
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # check that our booster got its lqty
    assert booster.stakedBalance() > 0

    ################# GENERATE CLAIMABLE PROFIT HERE AS NEEDED #################
    # we simulate minting LUSD fees from liquity's borrower operations to the staking contract
    lusd_borrower = accounts.at(
        "0xaC5406AEBe35A27691D62bFb80eeFcD7c0093164", force=True
    )
    borrower_operations = accounts.at(
        "0x24179CD81c9e782A4096035f7eC97fB8B783e007", force=True
    )
    staking = Contract("0x4f9Fbb3f1E99B56e0Fe2892e623Ed36A76Fc605d")
    before = staking.getPendingLUSDGain(lusd_borrower)
    staking.increaseF_LUSD(100_000e18, {"from": borrower_operations})
    after = staking.getPendingLUSDGain(lusd_borrower)
    assert after > before

    # check that we have claimable profit on our booster
    claimable_lusd = staking.getPendingLUSDGain(booster)
    print("Claimable LUSD:", claimable_lusd / 1e18)

    # simulate profits
    chain.sleep(sleep_time)

    # need second harvest to get some profits sent to booster (ySwaps)
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # set our keep to zero
    strategy.setKeepLqty(0, {"from": gov})

    # simulate profits
    chain.sleep(sleep_time)

    # harvest so we get one with no keep
    (profit, loss) = harvest_strategy(
        use_yswaps,
        strategy,
        token,
        gov,
        profit_whale,
        profit_amount,
        destination_strategy,
    )

    # record this here so it isn't affected if we donate via ySwaps
    strategy_assets = strategy.estimatedTotalAssets()

    # harvest again so the strategy reports the final bit of profit for yswaps
    if use_yswaps:
        print("Using ySwaps for harvests")
        (profit, loss) = harvest_strategy(
            use_yswaps,
            strategy,
            token,
            gov,
            profit_whale,
            profit_amount,
            destination_strategy,
        )

    # evaluate our current total assets
    new_assets = vault.totalAssets()

    # confirm we made money, or at least that we have about the same
    if is_slippery and no_profit:
        assert pytest.approx(new_assets, rel=RELATIVE_APPROX) == old_assets
    else:
        new_assets >= old_assets

    # simulate five days of waiting for share price to bump back up
    chain.sleep(86400 * 5)
    chain.mine(1)

    # Display estimated APR
    print(
        "\nEstimated APR: ",
        "{:.2%}".format(
            ((new_assets - old_assets) * (365 * 86400 / sleep_time)) / (strategy_assets)
        ),
    )

    # withdraw and confirm we made money, or at least that we have about the same
    vault.withdraw({"from": whale})
    if is_slippery and no_profit:
        assert (
            pytest.approx(token.balanceOf(whale), rel=RELATIVE_APPROX) == starting_whale
        )
    else:
        assert token.balanceOf(whale) >= starting_whale


# test sweeping out tokens
def test_sweeps_and_harvest(
    gov,
    token,
    vault,
    whale,
    strategy,
    to_sweep,
    amount,
    profit_whale,
    profit_amount,
    destination_strategy,
    use_yswaps,
    lusd_whale,
    booster,
):
    # collect our tokens
    lqty = interface.IERC20(strategy.want())
    lusd = interface.IERC20(strategy.lusd())

    # lusd whale sends lusd to our booster
    lusd.transfer(booster, 2000e18, {"from": lusd_whale})

    # we can sweep out any non-want
    booster.sweep(strategy.lusd(), {"from": gov})

    # lusd whale sends ether and lusd to our booster, profit whale sends in lqty
    lusd.transfer(booster, 2000e18, {"from": lusd_whale})
    lusd_whale.transfer(booster, 1e18)
    lqty.transfer(booster, 100e18, {"from": profit_whale})

    # we can sweep out any non-want, do twice for zero sweep
    booster.sweep(strategy.lusd(), {"from": gov})
    booster.sweep(strategy.lusd(), {"from": gov})

    # only gov can sweep
    with brownie.reverts():
        booster.sweep(strategy.lusd(), {"from": whale})

    # not even gov can sweep lqty
    with brownie.reverts():
        booster.sweep(strategy.want(), {"from": gov})

    # lusd whale sends more lusd to our booster
    lusd.transfer(booster, 2000e18, {"from": lusd_whale})

    # can't do it before we sleep, for some reason coverage doesn't pick up on this 🤔
    assert chain.time() < booster.unstakeQueued() + (14 * 86400)
    with brownie.reverts():
        booster.unstakeAndSweep(2**256 - 1, {"from": gov})

    # queue our sweep, gotta wait two weeks before we can sweep tho
    booster.queueSweep({"from": gov})
    chain.sleep(86400 * 15)
    chain.mine(1)

    # lock some lqty, but only strategy can
    with brownie.reverts():
        booster.strategyHarvest({"from": gov})
    booster.strategyHarvest({"from": strategy})

    # sweep!
    booster.unstakeAndSweep(booster.stakedBalance(), {"from": gov})

    # only gov can sweep
    with brownie.reverts():
        booster.unstakeAndSweep(booster.stakedBalance(), {"from": whale})

    chain.sleep(1)
    chain.mine(1)

    # harvest with no stake and no lqty
    booster.strategyHarvest({"from": strategy})

    # check
    assert booster.stakedBalance() == 0

    # harvest with no stake and some lqty
    lqty.transfer(booster, 100e18, {"from": profit_whale})
    booster.strategyHarvest({"from": strategy})

    # check
    assert booster.stakedBalance() > 0
    booster.strategyHarvest({"from": strategy})

    # sweep again!
    booster.unstakeAndSweep(2**256 - 1, {"from": gov})

    # sweep again!
    booster.unstakeAndSweep(2**256 - 1, {"from": gov})

    # check
    assert booster.stakedBalance() == 0

    # one last harvest
    booster.strategyHarvest({"from": strategy})

    # lusd whale sends ether and lusd to our booster
    lusd.transfer(booster, 2000e18, {"from": lusd_whale})
    lusd_whale.transfer(booster, 1e18)

    # send it back out
    booster.unstakeAndSweep(2**256 - 1, {"from": gov})

    # send in more
    lusd.transfer(booster, 2000e18, {"from": lusd_whale})
    lusd_whale.transfer(booster, 1e18)

    # booster should have balance, then we harvest to make it go away (to strategy)
    assert booster.balance() > 0
    booster.strategyHarvest({"from": strategy})
    assert booster.balance() == 0

    # can't sweep if we wait too long, oops
    chain.sleep(14 * 86400)
    with brownie.reverts():
        booster.unstakeAndSweep(2**256 - 1, {"from": gov})
