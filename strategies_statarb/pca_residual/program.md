# pca_residual

PCA-residual mean reversion на топ-30 USDT-perp универсе. Раз в неделю
снимаем 90-дневное окно log-цен, стандартизуем, PCA на K=3
компоненты, для каждого символа считаем residual = standardized − projection,
проверяем стационарность (ADF) и half-life. Для прошедших фильтр —
строим basket с весами, восстанавливающими residual из log-цен, торгуем
z-score спреда.

## Iter log

(none yet — first run pending)
