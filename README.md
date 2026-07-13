# Техно-Бро

Прототип многостраничного интернет-магазина гаджетов и электроники. Next.js 16
(App Router), TypeScript (strict), Tailwind CSS v4. 32 товара, 8 категорий,
локальное состояние (корзина, избранное, сравнение) на React Context + localStorage.

## Стек

- **Next.js 16** (App Router, Turbopack) — минимальное требование задачи было «14+»,
  использована актуальная стабильная версия `create-next-app@latest`.
- **TypeScript** в строгом режиме (`strict: true`).
- **Tailwind CSS v4** — дизайн-токены заданы через CSS `@theme` в
  `src/app/globals.css` (в Tailwind v4 нет `tailwind.config.ts` по умолчанию,
  цвета/шрифт объявляются как CSS-переменные).
- **React 19**, локальное состояние без внешних стейт-менеджеров.

## Запуск локально

```bash
npm install
npm run dev
```

Приложение будет доступно на [http://localhost:3000](http://localhost:3000).

## Проверка перед коммитом / релизом

```bash
npm run typecheck   # tsc --noEmit
npm run lint         # eslint
npm run build        # production-сборка
npm run start         # запуск собранного приложения
```

## Структура проекта

```
src/
  app/                 # роуты App Router (Server Components)
  components/          # UI, по доменам (layout, home, catalog, product, cart, checkout, compare, notifications)
  context/StateContext.tsx   # глобальное состояние: корзина, избранное, сравнение, уведомления
  hooks/useStore.ts     # доступ к StateContext
  data/                # products.ts (32 товара), categories.ts (8 категорий)
  types/index.ts         # Product, CartItem, CompareGroups и т.д.
  lib/                  # products-repo (выборки/поиск/фильтры), format, query, constants
```

## Маршруты

| Путь | Тип | Описание |
|---|---|---|
| `/` | Static | Hero, «Товар дня», сетка категорий |
| `/catalog` | Dynamic* | Общий каталог, фильтры/сортировка через `searchParams` |
| `/catalog/[category]` | Dynamic* | Категория, `generateStaticParams` по 8 slug'ам |
| `/product/[slug]` | SSG | Карточка товара, `generateStaticParams` по 32 товарам |
| `/search` | Dynamic* | Результаты поиска (`?query=`) |
| `/compare` | Static (client) | Таблица сравнения товаров одной категории |
| `/cart` | Static (client) | Корзина |
| `/checkout` | Static (client) | Оформление заказа, генерация номера `ТБ-XXXX` |
| `/discounts-list` | Static | Товары со скидкой, независимый top-level роут |

\* Страницы, читающие `searchParams`, Next.js всегда рендерит динамически (per-request) —
это стандартное поведение App Router, а не ошибка сборки. `generateStaticParams`
на `[category]`/`[slug]` при этом реализован и определяет валидные пути.

## Деплой на Vercel

1. Запушить репозиторий в GitHub/GitLab/Bitbucket.
2. На [vercel.com/new](https://vercel.com/new) импортировать репозиторий —
   Vercel автоматически определит Next.js и настройки сборки (`next build`).
3. Переменные окружения не требуются: все данные о товарах статические
   (`src/data/products.ts`), внешний backend не используется.
4. После деплоя домен `images.unsplash.com` уже разрешён в
   `next.config.ts` → `images.remotePatterns`, дополнительная настройка не нужна.

Либо через CLI:

```bash
npm i -g vercel
vercel
```
